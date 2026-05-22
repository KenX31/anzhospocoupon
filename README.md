# ANZ Hospo Coupon Activity Review

Streamlit report for the NZ restaurant exchange-rate coupon activity review.

## Data Architecture

- Public code repo: `git@github.com:KenX31/anzhospocoupon.git`
- Private data repo: `KenX31/anzdata`
- Private data project: `projects/anz-hospo-coupon/`
- Real data is loaded from the private repo through Streamlit secrets.
- Public repo contains code and small synthetic sample data only.

## Streamlit Secrets

```toml
HOSPO_COUPON_ACCESS_DIGEST = "replace-with-sha256-access-key-digest"

DATA_BACKEND = "github_private"
DATA_GITHUB_TOKEN = "replace-with-read-only-fine-grained-token"
DATA_GITHUB_REPO = "KenX31/anzdata"
DATA_GITHUB_REF = "main"
DATA_PROJECT = "anz-hospo-coupon"
DATA_VERSION = "2026-05-22-nz-restaurant-rate-coupon-v1"
```

To generate the access digest:

```powershell
python -c "import hashlib; print(hashlib.sha256('your-password'.encode()).hexdigest())"
```

## Local Preview

Sample data:

```powershell
$env:DATA_BACKEND='sample'
streamlit run app.py
```

Local private-data worktree:

```powershell
$env:DATA_BACKEND='local'
$env:LOCAL_DATA_ROOT='D:\Tencent\Data analysis\anzdata-worktree'
$env:DATA_PROJECT='anz-hospo-coupon'
streamlit run app.py
```

## Safety Check

```powershell
python scripts\check_public_repo_safety.py
```
