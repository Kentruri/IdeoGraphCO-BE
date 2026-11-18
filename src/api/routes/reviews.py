"""Fichas de revisión de la explicación en vivo (POST /api/v1/explanation-reviews).

La mesa llena la ficha con la salida estructurada de /explain (el apoyo por
clase y el párrafo) y quien revisa la corrige y la guarda. Se valida en el
cliente con Zod y aquí otra vez con Pydantic (ExplanationReviewRequestDTO):
clases conocidas, la primera igual a la predicha, un nivel de apoyo por clase
explicada, probabilidades que suman 1 y largos máximos.

Es el único lugar donde la mesa guarda el texto pegado, y solo cuando alguien
guarda la ficha. Sin autenticación: vive donde vive la explicación en vivo
(DESK_EXPLANATION_MODE=claude_cli, el entorno local de la tesis).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_desk_explanations
from src.db.models import ExplanationReview
from src.schemas.dto import (
    ExplanationReviewCreatedDTO,
    ExplanationReviewRequestDTO,
    FeatureDisabledErrorDTO,
)

router = APIRouter(prefix="/api/v1", tags=["classify"])


@router.post(
    "/explanation-reviews",
    status_code=201,
    response_model=ExplanationReviewCreatedDTO,
    dependencies=[Depends(require_desk_explanations)],
    responses={503: {"model": FeatureDisabledErrorDTO}},
)
def create_review(
    body: ExplanationReviewRequestDTO,
    db: Annotated[Session, Depends(get_db)],
) -> ExplanationReviewCreatedDTO:
    explanation = body.explanation
    review = ExplanationReview(
        id=uuid.uuid4().hex,
        title=body.title.strip(),
        text=body.text,
        classification_label=body.classification.label,
        classification_probabilities=body.classification.probabilities,
        classification_model_version=body.classification.model_version,
        explanation_text=explanation.text,
        explanation_classes=explanation.classes,
        explanation_support=explanation.support,
        explanation_model=explanation.model,
        explanation_prompt_version=explanation.prompt_version,
        reviewer_support=body.review.support,
        revised_text=body.review.revised_text,
        fits_text=body.review.fits_text,
        neutral=body.review.neutral,
        notes=body.review.notes,
    )
    db.add(review)
    db.commit()
    return ExplanationReviewCreatedDTO(id=review.id, created_at=review.created_at)
