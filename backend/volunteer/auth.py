import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}"

jwks = PyJWKClient(
    f"{ISSUER}/.well-known/jwks.json",
    timeout=5,
)
bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    unauthorized = HTTPException(
        status_code=401,
        detail="A valid Cognito access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    token = credentials.credentials
    if len(token) > 16384:
        raise unauthorized

    try:
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={
                "verify_aud": False,
                "require": [
                    "exp", "iat", "iss", "sub", "token_use", "client_id"
                ],
            },
        )
    except PyJWKClientConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Authentication service temporarily unavailable.",
        ) from None
    except jwt.PyJWTError:
        raise unauthorized from None

    # Cognito access tokens identify the app using client_id.
    if claims["token_use"] != "access" or claims["client_id"] != CLIENT_ID:
        raise unauthorized

    groups = claims.get("cognito:groups", [])
    if not isinstance(groups, list) or not all(
        isinstance(group, str) for group in groups
    ):
        raise unauthorized

    return {"userId": claims["sub"], "groups": groups}
