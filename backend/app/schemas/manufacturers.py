"""API response of the manufacturers resource."""

from pydantic import BaseModel, ConfigDict


class Manufacturer(BaseModel):
    """One manufacturer and what its registered catalogues cover."""

    model_config = ConfigDict(frozen=True)

    name: str
    category: str
    product_types: list[str]
    documents: int
