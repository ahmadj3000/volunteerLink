"""Run the local backend using temporary, limited AWS credentials."""
import json
import os
import subprocess

credentials = json.loads(subprocess.check_output(
    [
        "aws", "configure", "export-credentials",
        "--profile", "vl-ngo-dev", "--format", "process",
    ],
    text=True,
))

environment = os.environ.copy()
environment.update({
    "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
    "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
    "AWS_SESSION_TOKEN": credentials["SessionToken"],
    "AWS_DEFAULT_REGION": "us-east-1",
    "COGNITO_USER_POOL_ID": "us-east-1_is9qkiZoh",
    "COGNITO_CLIENT_ID": "6blnjutulha6k2gr0d1lf5fe9f",
})

command = [
    "docker", "run", "--rm", "--name", "vl-ngo-api",
    "-p", "127.0.0.1:8000:8000",
]
for name in (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION", "COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID",
):
    command.extend(["-e", name])
command.append("vl-ngo-api:dev")

print("Starting backend with temporary vl-ngo-dev credentials.", flush=True)
try:
    subprocess.run(command, env=environment, check=True)
except KeyboardInterrupt:
    subprocess.run(["docker", "stop", "vl-ngo-api"], check=False)
    raise SystemExit(130)
