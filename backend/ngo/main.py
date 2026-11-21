from fastapi import Depends, FastAPI

from auth import current_user

app = FastAPI(title="VolunteerLink NGO API")


@app.get("/health")
def health():
    return {"status": "ok", "service": "vl-ngo-api"}


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return user
