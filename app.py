from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


APP_TITLE = "NZ 餐饮汇率活动复盘"
DEFAULT_DATA_PROJECT = "anz-hospo-coupon"
SAMPLE_DATA_DIR = Path(__file__).resolve().parent / "data" / "sample" / "processed"
REQUIRED_FILES = {
    "overview_metrics": "overview_metrics.json",
    "restaurant_monthly_trend": "restaurant_monthly_trend.csv",
    "restaurant_pre_vs_activity_summary": "restaurant_pre_vs_activity_summary.csv",
    "merchant_scope_review_summary": "merchant_scope_review_summary.csv",
    "non_restaurant_category_summary": "non_restaurant_category_summary.csv",
    "priority_exclude_health_daigou_merchants": "priority_exclude_health_daigou_merchants.csv",
    "screening_rules": "screening_rules.md",
    "created_during_activity_summary": "created_during_activity_summary.csv",
    "first_active_summary": "first_active_summary.csv",
}
OPTIONAL_FILES = {
    "merchant_scope_review": "merchant_scope_review.csv",
    "all_redeeming_monthly_trend": "all_redeeming_monthly_trend.csv",
    "all_redeeming_pre_vs_activity_summary": "all_redeeming_pre_vs_activity_summary.csv",
}


class DataLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataBundle:
    frames: dict[str, pd.DataFrame]
    metrics: dict
    screening_rules: str
    manifest: dict


def get_secret_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = os.environ.get(name, default)
    if value is None:
        return default
    return str(value)


def sha256_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_access() -> None:
    digest = get_secret_value("HOSPO_COUPON_ACCESS_DIGEST").strip()
    if not digest:
        return
    if st.session_state.get("access_granted"):
        return

    st.title(APP_TITLE)
    st.caption("请输入访问密码。")
    password = st.text_input("访问密码", type="password")
    if not password:
        st.stop()
    if sha256_digest(password) != digest:
        st.error("密码不正确。")
        st.stop()
    st.session_state["access_granted"] = True
    st.rerun()


def get_data_backend() -> str:
    return get_secret_value("DATA_BACKEND", "sample").strip().lower() or "sample"


def get_data_project() -> str:
    return get_secret_value("DATA_PROJECT", DEFAULT_DATA_PROJECT).strip() or DEFAULT_DATA_PROJECT


def get_local_data_root() -> Path:
    configured = get_secret_value("LOCAL_DATA_ROOT").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "data"


def private_path(filename: str) -> str:
    return f"projects/{get_data_project().strip('/')}/processed/{filename}"


def private_manifest_path() -> str:
    return f"projects/{get_data_project().strip('/')}/manifest.json"


def github_private_bytes(path: str) -> bytes | None:
    token = get_secret_value("DATA_GITHUB_TOKEN").strip()
    repo = get_secret_value("DATA_GITHUB_REPO", "KenX31/anzdata").strip()
    ref = get_secret_value("DATA_GITHUB_REF", "main").strip() or "main"
    if not token or not repo:
        raise DataLoadError("私有数据仓未配置。请设置 DATA_GITHUB_TOKEN 和 DATA_GITHUB_REPO。")

    url = f"https://api.github.com/repos/{repo}/contents/{quote(path, safe='/')}"
    headers = {
        "Accept": "application/vnd.github.raw",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = requests.get(url, headers=headers, params={"ref": ref}, timeout=20)
    except requests.RequestException as exc:
        raise DataLoadError("无法连接私有数据仓，请检查部署网络和 GitHub token。") from exc
    if response.status_code == 404:
        return None
    if response.status_code in {401, 403}:
        raise DataLoadError("私有数据仓认证失败，请检查 GitHub token 权限。")
    if not response.ok:
        raise DataLoadError(f"私有数据仓读取失败：HTTP {response.status_code}")
    return response.content


def read_text_bytes(content: bytes) -> str:
    return content.decode("utf-8-sig")


def load_local_file(filename: str) -> bytes | None:
    backend = get_data_backend()
    if backend == "sample":
        path = SAMPLE_DATA_DIR / filename
    else:
        path = get_local_data_root() / "projects" / get_data_project() / "processed" / filename
    if not path.exists():
        return None
    return path.read_bytes()


def load_manifest() -> dict:
    backend = get_data_backend()
    if backend == "github_private":
        content = github_private_bytes(private_manifest_path())
    elif backend in {"sample", "local"}:
        if backend == "sample":
            manifest_path = SAMPLE_DATA_DIR.parent / "manifest.json"
        else:
            manifest_path = get_local_data_root() / "projects" / get_data_project() / "manifest.json"
        content = manifest_path.read_bytes() if manifest_path.exists() else None
    else:
        raise DataLoadError(f"不支持的数据源类型：{backend}")
    if not content:
        return {}
    return json.loads(read_text_bytes(content))


def data_version() -> str:
    configured = get_secret_value("DATA_VERSION").strip()
    if configured:
        return configured
    if get_data_backend() == "github_private":
        manifest = load_manifest()
        ref = get_secret_value("DATA_GITHUB_REF", "main").strip() or "main"
        return f"{ref}:{manifest.get('version', '')}"
    return "local-or-sample"


@st.cache_data(show_spinner=False)
def load_data(version: str) -> DataBundle:
    backend = get_data_backend()
    manifest = load_manifest()
    frames: dict[str, pd.DataFrame] = {}
    metrics: dict = {}
    screening_rules = ""

    for key, filename in {**REQUIRED_FILES, **OPTIONAL_FILES}.items():
        if backend == "github_private":
            content = github_private_bytes(private_path(filename))
        elif backend in {"sample", "local"}:
            content = load_local_file(filename)
        else:
            raise DataLoadError(f"不支持的数据源类型：{backend}")

        if content is None:
            if key in REQUIRED_FILES:
                raise DataLoadError(f"数据文件缺失：{filename}")
            continue
        if filename.endswith(".csv"):
            frames[key] = pd.read_csv(BytesIO(content), encoding="utf-8-sig")
        elif filename.endswith(".json"):
            metrics = json.loads(read_text_bytes(content))
        elif filename.endswith(".md"):
            screening_rules = read_text_bytes(content)

    return DataBundle(frames=frames, metrics=metrics, screening_rules=screening_rules, manifest=manifest)


def fmt_int(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.0f}"


def fmt_money(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    abs_value = abs(float(value))
    if abs_value >= 1_000_000:
        return f"RMB {float(value) / 1_000_000:,.2f}M"
    if abs_value >= 1_000:
        return f"RMB {float(value) / 1_000:,.1f}K"
    return f"RMB {float(value):,.0f}"


def fmt_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:.1f}%"


def kpi_card(label: str, value: str, helper: str = "") -> None:
    st.markdown(
        f"""
        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{value}</div>
          <div class="kpi-helper">{helper}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def inject_style() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; max-width: 1200px; }
        .kpi-card {
            border: 1px solid #d8dee9;
            border-radius: 8px;
            padding: 14px 16px;
            background: #fff;
            min-height: 118px;
        }
        .kpi-label { color: #5d6675; font-size: 0.86rem; line-height: 1.25; }
        .kpi-value { font-size: 1.7rem; font-weight: 700; margin-top: 8px; color: #172033; }
        .kpi-helper { color: #6f7a89; font-size: 0.82rem; margin-top: 8px; line-height: 1.35; }
        .callout {
            border-left: 4px solid #d04f2f;
            background: #fff5f0;
            padding: 12px 14px;
            margin: 8px 0 16px;
            color: #3f2a20;
        }
        .note {
            border-left: 4px solid #4c78a8;
            background: #f3f7fb;
            padding: 12px 14px;
            margin: 8px 0 16px;
            color: #1f3347;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalized_period(label: str) -> str:
    if str(label).startswith("baseline"):
        return "活动前"
    if str(label).startswith("activity"):
        return "活动期"
    return str(label)


def overview_page(bundle: DataBundle) -> None:
    metrics = bundle.metrics
    all_scope = metrics.get("all_redeeming", {})
    by_scope = metrics.get("by_scope", {})
    restaurant = by_scope.get("restaurant_include", {})
    non_restaurant = by_scope.get("non_restaurant_exclude", {})

    st.subheader("总览")
    st.markdown(
        '<div class="callout">主结论使用确认餐饮商户口径。全量核销口径包含历史配置误纳入的非餐饮商户，只作为偏差说明。</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(4)
    with cols[0]:
        kpi_card("全量核销商户", fmt_int(all_scope.get("merchant_count")), "原始批次口径")
    with cols[1]:
        kpi_card("确认餐饮商户", fmt_int(restaurant.get("merchant_count")), "主分析口径")
    with cols[2]:
        kpi_card("确认非餐饮成本", fmt_money(non_restaurant.get("cost_money_yuan")), "误纳入配置偏差")
    with cols[3]:
        kpi_card("活动总成本", fmt_money(all_scope.get("cost_money_yuan")), "全量核销口径")

    st.markdown("#### 商户范围拆分")
    scope_chart = pd.DataFrame(
        [
            {
                "商户范围": "确定餐饮",
                "活动成本RMB": restaurant.get("cost_money_yuan", 0),
                "merchant_count": restaurant.get("merchant_count", 0),
            },
            {
                "商户范围": "确认非餐饮",
                "活动成本RMB": non_restaurant.get("cost_money_yuan", 0),
                "merchant_count": non_restaurant.get("merchant_count", 0),
            },
        ]
    )
    scope_chart["活动成本"] = scope_chart["活动成本RMB"].map(fmt_money)
    fig = px.bar(
        scope_chart,
        x="商户范围",
        y="活动成本RMB",
        color="商户范围",
        text="活动成本",
        custom_data=["merchant_count"],
        labels={"活动成本RMB": "活动成本 RMB", "商户范围": "商户范围"},
    )
    fig.update_traces(
        textposition="outside",
        hovertemplate="商户范围=%{x}<br>活动成本=%{text}<br>商户数=%{customdata[0]:,.0f}<extra></extra>",
    )
    fig.update_layout(
        legend_title_text="商户范围",
        height=380,
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="活动成本 RMB",
    )
    st.plotly_chart(fig, use_container_width=True)

    restaurant_effect_page(bundle)
    new_active_page(bundle)


def restaurant_effect_page(bundle: DataBundle) -> None:
    created = bundle.frames["created_during_activity_summary"].iloc[0].to_dict()
    first = bundle.frames["first_active_summary"].iloc[0].to_dict()

    st.subheader("确认餐饮商户成效")
    st.markdown(
        (
            '<div class="callout">以下 KPI 是固定核销餐饮 MID 口径，包含活动期新接入/新产生交易的商户，'
            f'其中活动期新增/创建 {fmt_int(created.get("created_during_activity_merchant_count"))} 家，'
            f'活动期首次活跃 {fmt_int(first.get("first_active_during_activity_merchant_count"))} 家。'
            "因此它适合看活动覆盖商户整体变化，不宜直接解读为严格同店促活。</div>"
        ),
        unsafe_allow_html=True,
    )
    lift = bundle.metrics.get("restaurant_activity_lift", {})
    cols = st.columns(3)
    with cols[0]:
        kpi_card("平均活跃商户数", f"{fmt_int(lift.get('baseline_avg_active_merchants'))} → {fmt_int(lift.get('activity_avg_active_merchants'))}", f"提升 {fmt_pct(lift.get('active_merchants_lift_rate'))}")
    with cols[1]:
        kpi_card("近30天交易笔数", f"{fmt_int(lift.get('baseline_avg_rolling30_trade_count'))} → {fmt_int(lift.get('activity_avg_rolling30_trade_count'))}", f"提升 {fmt_pct(lift.get('trade_count_lift_rate'))}")
    with cols[2]:
        kpi_card("近30天交易金额", f"{fmt_money(lift.get('baseline_avg_rolling30_trade_money_yuan'))} → {fmt_money(lift.get('activity_avg_rolling30_trade_money_yuan'))}", f"提升 {fmt_pct(lift.get('trade_money_lift_rate'))}")

    st.markdown('<div class="note">口径：月末滚动30天交易快照，不是自然月精确交易总额。</div>', unsafe_allow_html=True)
    monthly = bundle.frames["restaurant_monthly_trend"].copy()
    monthly["period"] = monthly["period_label"].map(normalized_period)
    monthly["month_label"] = monthly["month_label"].astype(str)
    fig_txn = px.line(
        monthly,
        x="month_label",
        y="rolling30_trade_count",
        color="period",
        markers=True,
        labels={"rolling30_trade_count": "近30天交易笔数", "month_label": "月份", "period": "时期"},
    )
    fig_txn.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=20, b=10),
        legend_title_text="时期",
        yaxis_title="近30天交易笔数",
    )
    st.plotly_chart(fig_txn, use_container_width=True)

    fig_active = px.line(
        monthly,
        x="month_label",
        y="active_restaurant_merchant_count_rolling30",
        color="period",
        markers=True,
        labels={"active_restaurant_merchant_count_rolling30": "近30天活跃餐饮商户数", "month_label": "月份", "period": "时期"},
    )
    fig_active.update_layout(
        height=380,
        margin=dict(l=10, r=10, t=20, b=10),
        legend_title_text="时期",
        yaxis_title="近30天活跃餐饮商户数",
    )
    st.plotly_chart(fig_active, use_container_width=True)


def fairness_page(bundle: DataBundle) -> None:
    st.subheader("公平性与配置偏差")
    metrics = bundle.metrics
    by_scope = metrics.get("by_scope", {})
    non_restaurant = by_scope.get("non_restaurant_exclude", {})
    all_scope = metrics.get("all_redeeming", {})
    cost_share = (non_restaurant.get("cost_money_yuan", 0) / all_scope.get("cost_money_yuan", 1)) if all_scope.get("cost_money_yuan") else 0

    cols = st.columns(3)
    with cols[0]:
        kpi_card("确认非餐饮商户", fmt_int(non_restaurant.get("merchant_count")), "从主口径剔除")
    with cols[1]:
        kpi_card("非餐饮成本", fmt_money(non_restaurant.get("cost_money_yuan")), f"占全量成本 {fmt_pct(cost_share)}")
    with cols[2]:
        kpi_card("非餐饮支付金额", fmt_money(non_restaurant.get("pay_amt_cny_yuan")), "全量口径主要偏差来源")

    st.markdown(
        '<div class="callout">保健品、代购、云仓/批发和礼品零售不只是行业不匹配，消费者也可能并非新西兰本地餐饮客群。</div>',
        unsafe_allow_html=True,
    )
    category = bundle.frames["non_restaurant_category_summary"].copy()
    fig = px.bar(
        category,
        y="non_restaurant_category",
        x="cost_money_yuan",
        orientation="h",
        text="merchant_count",
        labels={"cost_money_yuan": "活动成本 RMB", "non_restaurant_category": "非餐饮类别"},
    )
    fig.update_layout(height=440, margin=dict(l=10, r=10, t=20, b=10), yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 高优先级排除商户")
    priority = bundle.frames["priority_exclude_health_daigou_merchants"].copy()
    display_cols = [c for c in ["merchant_shortname", "merchant_name", "mcc_code", "pay_amt_cny_yuan", "cost_money_yuan", "non_restaurant_refined_category"] if c in priority.columns]
    st.dataframe(priority[display_cols].head(30), use_container_width=True, hide_index=True)


def new_active_page(bundle: DataBundle) -> None:
    st.subheader("新增与首次活跃")
    created = bundle.frames["created_during_activity_summary"].iloc[0].to_dict()
    first = bundle.frames["first_active_summary"].iloc[0].to_dict()
    cols = st.columns(3)
    with cols[0]:
        kpi_card("活动期新增/创建", fmt_int(created.get("created_during_activity_merchant_count")), fmt_pct(created.get("created_during_activity_merchant_share")))
    with cols[1]:
        kpi_card("活动期首次活跃", fmt_int(first.get("first_active_during_activity_merchant_count")), fmt_pct(first.get("first_active_during_activity_merchant_share")))
    with cols[2]:
        kpi_card("无滚动30天活跃记录", fmt_int(first.get("merchants_with_no_rolling30_activity_observed")), "确认餐饮白名单内")
    st.markdown(
        '<div class="note">这部分会抬高活动期增长，不能完全解释为存量餐饮商户被活动重新激活。</div>',
        unsafe_allow_html=True,
    )


def merchant_detail_page(bundle: DataBundle) -> None:
    st.subheader("商户明细")
    detail = bundle.frames.get("merchant_scope_review")
    if detail is None or detail.empty:
        st.info("当前数据包未包含商户明细文件。")
        return

    df = detail.copy()
    df["cost_money_yuan"] = pd.to_numeric(df.get("cost_money_yuan"), errors="coerce").fillna(0)
    df["pay_amt_cny_yuan"] = pd.to_numeric(df.get("pay_amt_cny_yuan"), errors="coerce").fillna(0)
    df["save_money_yuan"] = pd.to_numeric(df.get("save_money_yuan"), errors="coerce").fillna(0)
    df["coupon_txn_cnt"] = pd.to_numeric(df.get("coupon_txn_cnt"), errors="coerce").fillna(0)
    df = df.sort_values("cost_money_yuan", ascending=False)
    df["scope_label"] = df["scope_flag"].map(
        {
            "restaurant_include": "确定餐饮",
            "non_restaurant_exclude": "确认非餐饮",
            "needs_review": "待复核",
        }
    ).fillna(df["scope_flag"])

    display_cols = [
        "scope_label",
        "sub_mchid",
        "merchant_shortname",
        "merchant_name",
        "company_name",
        "mcc_code",
        "business_category",
        "business_type",
        "coupon_txn_cnt",
        "pay_amt_cny_yuan",
        "cost_money_yuan",
        "save_money_yuan",
        "review_reason",
        "stores_address",
    ]
    display_cols = [col for col in display_cols if col in df.columns]
    display = df[display_cols].rename(
        columns={
            "scope_label": "范围",
            "sub_mchid": "商户ID",
            "merchant_shortname": "商户简称",
            "merchant_name": "商户名",
            "company_name": "公司名",
            "mcc_code": "MCC",
            "business_category": "业务类目",
            "business_type": "线上/线下",
            "coupon_txn_cnt": "核销笔数",
            "pay_amt_cny_yuan": "核销支付金额RMB",
            "cost_money_yuan": "活动成本RMB",
            "save_money_yuan": "用户节省RMB",
            "review_reason": "复核原因",
            "stores_address": "门店地址",
        }
    )

    st.markdown('<div class="note">按活动成本从高到低排序；确认非餐饮商户已标红，优先用于复核配置偏差和成本占用。</div>', unsafe_allow_html=True)

    def highlight_non_restaurant(row: pd.Series) -> list[str]:
        if row.get("范围") == "确认非餐饮":
            return ["background-color: #ffe1e1; color: #7a1616; font-weight: 600"] * len(row)
        return [""] * len(row)

    styled = display.style.apply(highlight_non_restaurant, axis=1).format(
        {
            "核销笔数": "{:,.0f}",
            "核销支付金额RMB": "{:,.2f}",
            "活动成本RMB": "{:,.2f}",
            "用户节省RMB": "{:,.2f}",
        },
        na_rep="-",
    )
    st.dataframe(styled, use_container_width=True, hide_index=True, height=680)


def rules_page(bundle: DataBundle) -> None:
    st.subheader("重启活动筛查规则")
    st.markdown(bundle.screening_rules)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    inject_style()
    require_access()

    try:
        bundle = load_data(data_version())
    except DataLoadError as exc:
        st.error(str(exc))
        st.stop()

    st.title(APP_TITLE)
    st.caption("NZ 餐饮汇率活动｜确认餐饮口径 + 配置偏差复盘")

    tabs = st.tabs(["总览", "公平性/偏差", "商户明细", "重启规则"])
    with tabs[0]:
        overview_page(bundle)
    with tabs[1]:
        fairness_page(bundle)
    with tabs[2]:
        merchant_detail_page(bundle)
    with tabs[3]:
        rules_page(bundle)

    st.divider()
    source = bundle.manifest.get("source", {}) if bundle.manifest else {}
    st.caption(
        f"Data project: {get_data_project()} | "
        f"Baseline: {source.get('baseline_window', '2024-04 to 2025-03')} | "
        f"Activity: {source.get('activity_window', '2025-04 to 2026-04')} | "
        f"Backend: {get_data_backend()}"
    )


if __name__ == "__main__":
    main()
