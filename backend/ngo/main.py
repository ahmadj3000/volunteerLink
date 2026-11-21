from fastapi import FastAPI

app = FastAPI(title="VolunteerLink NGO API")


@app.get("/health")
def health():
    return {"status": "ok", "service": "vl-ngo-api"}
