from fastapi import Depends, FastAPI, HTTPException

from auth import current_user

app = FastAPI(title="VolunteerLink NGO API")


def require_group(group: str):
    def check(user: dict = Depends(current_user)):
        if group not in user["groups"]:
            raise HTTPException(
                status_code=403,
                detail=f"The {group} role is required.",
            )
        return user
    return check


@app.get("/health")
def health():
    return {"status": "ok", "service": "vl-ngo-api"}


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/ngo/access")
def ngo_access(user: dict = Depends(require_group("ngo"))):
    return {"message": "NGO role verified.", **user}


@app.get("/admin/access")
def admin_access(user: dict = Depends(require_group("admin"))):
    return {"message": "Admin role verified.", **user}
