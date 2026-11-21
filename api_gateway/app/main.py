import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="Microservices API Gateway", version="0.1.0")
SERVICES = {
    "auth": os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8001"),
    "products": os.getenv("PRODUCT_SERVICE_URL", "http://127.0.0.1:8002"),
    "orders": os.getenv("ORDER_SERVICE_URL", "http://127.0.0.1:8004"),
    "payments": os.getenv("PAYMENT_SERVICE_URL", "http://127.0.0.1:8003"),
}
RESOURCE_ROOTS = {"auth": "", "products": "products", "orders": "orders", "payments": "payments"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "api-gateway"}


@app.api_route("/api/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.api_route("/api/{service}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(service: str, request: Request, path: str = ""):
    base_url = SERVICES.get(service)
    if not base_url:
        return JSONResponse(status_code=404, content={"detail": "Unknown service"})

    resource_root = RESOURCE_ROOTS[service]
    if resource_root:
        upstream_path = f"/{resource_root}" + (f"/{path}" if path else "")
    else:
        upstream_path = f"/{path}" if path else "/"
    upstream_url = f"{base_url}{upstream_path}"

    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            upstream = await client.request(
                method=request.method,
                url=upstream_url,
                params=request.query_params,
                content=body or None,
                headers=headers,
            )
        response_headers = {
            key: value for key, value in upstream.headers.items()
            if key.lower() in {"content-type", "cache-control"}
        }
        return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
    except httpx.RequestError:
        return JSONResponse(status_code=503, content={"detail": f"{service} service unavailable"})
