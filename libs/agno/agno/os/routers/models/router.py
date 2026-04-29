import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, HTTPException, Path

from agno.db.base import BaseDb
from agno.models.base import get_model_by_id
from agno.models.message import Message
from agno.os.auth import get_authentication_dependency
from agno.os.routers.models.schema import ModelTestRequest, ModelTestResponse
from agno.os.schema import (
    BadRequestResponse,
    InternalServerErrorResponse,
    NotFoundResponse,
    UnauthenticatedResponse,
    ValidationErrorResponse,
)
from agno.os.settings import AgnoAPISettings
from agno.utils.log import log_error

if TYPE_CHECKING:
    from agno.os.app import AgentOS

logger = logging.getLogger(__name__)


def get_model_router(
    os: "AgentOS",
    settings: AgnoAPISettings = AgnoAPISettings(),
) -> APIRouter:
    router = APIRouter(
        dependencies=[Depends(get_authentication_dependency(settings))],
        tags=["Models"],
        responses={
            400: {"description": "Bad Request", "model": BadRequestResponse},
            401: {"description": "Unauthorized", "model": UnauthenticatedResponse},
            404: {"description": "Not Found", "model": NotFoundResponse},
            422: {"description": "Validation Error", "model": ValidationErrorResponse},
            500: {"description": "Internal Server Error", "model": InternalServerErrorResponse},
        },
    )

    if not isinstance(os.db, BaseDb):
        raise ValueError("Model routes require a sync database (BaseDb), not an async database.")
    db: BaseDb = os.db

    @router.post(
        "/models/{component_id}/test",
        response_model=ModelTestResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="test_model",
        summary="Test Model",
        description="Load a saved Model from the database and send a test message to verify connectivity.",
    )
    async def test_model(
        component_id: str = Path(description="Component ID of the model to test"),
        body: ModelTestRequest = Body(default=ModelTestRequest()),
    ) -> ModelTestResponse:
        try:
            model = get_model_by_id(component_id=component_id, db=db)
            if model is None:
                raise HTTPException(status_code=404, detail=f"Model {component_id} not found")

            messages = [Message(role="user", content=body.message)]
            model_response = model.response(messages=messages)
            return ModelTestResponse(
                component_id=component_id,
                model_id=model.id,
                provider=model.provider,
                success=True,
                response=model_response.content,
            )
        except HTTPException:
            raise
        except Exception as e:
            log_error(f"Error testing model {component_id}: {str(e)}")
            return ModelTestResponse(
                component_id=component_id,
                success=False,
                error=str(e),
            )

    return router
