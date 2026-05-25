import json
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.params import Header

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/webhook", status_code=200)
async def webhook(request: Request, x_hub_signature_256: Annotated[str|None, Header()]=None):
    body = await request.body()
    parsed_body = json.loads(body)
    print(json.dumps(parsed_body, indent=2))
    # validate, log, return
    if x_hub_signature_256 != "something":
        raise HTTPException(status_code=401, detail="Invalid signature provided")
    return {"ok": True}
