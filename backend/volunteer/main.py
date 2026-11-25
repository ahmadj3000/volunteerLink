"""VolunteerLink Volunteer API — runs as the Volunteer-account ECS service.

Modules (one router each, one shared app/entrypoint, one requirements.txt):
  profiles.py      Ahmad  — GET/PUT /me/profile (migrated from Lambda)
  applications.py  Ahmad  — POST /applications, GET /me/applications
  ngo_client.py    Ahmad  — calls the NGO account's internal API (SigV4 in step 3c)
  matching.py      Tarek  — embeddings, cosine ranking, recommendations (next)
"""

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import applications
import profiles

app = FastAPI(title="VolunteerLink Volunteer API")
app.include_router(profiles.router)
app.include_router(applications.router)


@app.exception_handler(ClientError)
async def aws_error(request, error):
    # log the AWS error code, never leak internals to the client
    print(f"AWS operation failed: {error.response['Error']['Code']}", flush=True)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable."})


@app.exception_handler(BotoCoreError)
async def connection_error(request, error):
    print(f"AWS connection failed: {type(error).__name__}", flush=True)
    return JSONResponse(status_code=503, content={"detail": "AWS connection unavailable."})


@app.get("/health")
def health():
    return {"status": "ok", "service": "vl-volunteer-api"}
