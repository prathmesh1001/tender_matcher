"""Document routes."""

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.api.deps import DocumentServiceDep

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get(
    "/{sha256}/file",
    response_class=Response,
    response_model=None,
    status_code=status.HTTP_200_OK,
)
def get_document_file(
    sha256: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], service: DocumentServiceDep
) -> Response:
    """A stored document, to open in the browser; add `#page=N` to open it at a page."""
    stored = service.get_file(sha256)
    return Response(
        content=stored.content,
        media_type=stored.media_type,
        headers={"Content-Disposition": f'inline; filename="{stored.filename}"'},
    )
