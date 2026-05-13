from fastapi import APIRouter
from typing import Any


class BaseAPIRouter(APIRouter):

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

    def get(self, path: str, **kwargs):
        kwargs.setdefault("response_model_exclude_none", True)
        return super().get(path, **kwargs)
    
    def post(self, path: str, **kwargs):
        kwargs.setdefault("response_model_exclude_none", True)
        return super().post(path, **kwargs)
    
    def put(self, path: str, **kwargs):
        kwargs.setdefault("response_model_exclude_none", True)
        return super().put(path, **kwargs)
    
    def delete(self, path: str, **kwargs):
        kwargs.setdefault("response_model_exclude_none", True)
        return super().delete(path, **kwargs)