# routers/inference.py — Endpoints de inferencia ML/DL via Orquestador
# Proxy al orchestrator + crea RiskReport cuando la inferencia finaliza
# Si el orquestador no está disponible, simula la respuesta localmente

import os
import httpx
import random
import uuid
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Patient, RiskReport, AuditLog
from auth import get_current_user, require_medico_or_admin, User
from schemas import InferenceMLRequest, InferenceDLRequest

router = APIRouter(prefix="/inference", tags=["Inference"])

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8003")

# Almacen local de tareas (cuando no hay orquestador)
_local_tasks = {}


def log_audit(db, user_id, action, resource_type, resource_id=None):
    entry = AuditLog(user_id=user_id, action=action, resource_type=resource_type, resource_id=resource_id)
    db.add(entry)
    db.commit()


def _simulate_ml(features: dict) -> dict:
    """Genera una prediccion ML mock coherente basada en los features."""
    glucose = features.get("Glucose", 120)
    bmi = features.get("BMI", 28)
    age = features.get("Age", 45)
    bp = features.get("BloodPressure", 80)
    insulin = features.get("Insulin", 85)

    # Score simple basado en features
    score = 0.0
    score += max(0, (glucose - 100)) * 0.003
    score += max(0, (bmi - 25)) * 0.02
    score += max(0, (age - 40)) * 0.005
    score += max(0, (bp - 120)) * 0.003
    score += max(0, (insulin - 100)) * 0.001
    score = min(max(score, 0.05), 0.95)

    if score > 0.6:
        category = "HIGH"
    elif score > 0.35:
        category = "MEDIUM"
    else:
        category = "LOW"

    # SHAP values simulados
    feature_names = ["Glucose", "BloodPressure", "BMI", "Insulin", "Age",
                     "Pregnancies", "SkinThickness", "DiabetesPedigreeFunction"]
    base_shaps = [0.25, 0.08, 0.18, 0.12, 0.10, 0.05, 0.04, 0.06]
    shap_values = {}
    for i, name in enumerate(feature_names):
        val = base_shaps[i] * (0.6 + random.random() * 0.8)
        if random.random() > 0.4:
            val = -val
        shap_values[name] = round(val, 4)

    # El feature mas alto en positivo marca la tendencia
    shap_values["Glucose"] = abs(shap_values["Glucose"]) * (1 if glucose > 140 else -1)
    shap_values["BMI"] = abs(shap_values["BMI"]) * (1 if bmi > 30 else -1)

    return {
        "probability": round(score, 4),
        "risk_category": category,
        "risk_prediction": f"{'Alto' if category == 'HIGH' else 'Moderado' if category == 'MEDIUM' else 'Bajo'} riesgo de diabetes tipo 2",
        "model_version": "pima-diabetes-v1.0-mock",
        "shap_values": shap_values,
    }


def _simulate_dl(patient_id: str) -> dict:
    """Genera una prediccion DL mock."""
    prob = round(random.uniform(0.1, 0.8), 4)
    category = "HIGH" if prob > 0.6 else "MEDIUM" if prob > 0.35 else "LOW"
    return {
        "probability": prob,
        "risk_category": category,
        "risk_prediction": f"Retinopatia diabetica - Riesgo {category}",
        "model_version": "retinopathy-v1.0-mock",
        "gradcam_url": None,
    }


@router.post("/ml")
async def run_ml_inference(
    body: InferenceMLRequest,
    current_user: User = Depends(require_medico_or_admin),
    db: Session = Depends(get_db),
):
    """Lanzar inferencia ML tabular para un paciente."""
    patient = db.query(Patient).filter(
        Patient.id == body.patient_id,
        Patient.deleted_at.is_(None),
    ).first()
    if not patient:
        raise HTTPException(404, "Paciente no encontrado")

    # Intentar usar orquestador, fallback a local
    task_id = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/infer", json={
                "patient_id": str(body.patient_id),
                "model_type": "ML",
                "features": body.features,
            })
            resp.raise_for_status()
            result = resp.json()
            task_id = result["task_id"]
    except Exception:
        # Orquestador no disponible -> simular localmente
        ml_result = _simulate_ml(body.features)
        _local_tasks[task_id] = {
            "task_id": task_id,
            "status": "DONE",
            "model_type": "ML",
            "result": ml_result,
        }
        # Crear RiskReport directamente
        report = RiskReport(
            patient_id=body.patient_id,
            model_type="ML",
            risk_score=ml_result["probability"],
            risk_category=ml_result["risk_category"],
            risk_prediction=ml_result["risk_prediction"],
            shap_values=ml_result["shap_values"],
        )
        db.add(report)
        log_audit(db, current_user.id, "INFERENCE_ML", "RiskReport", str(report.id))
        return {"task_id": task_id, "status": "DONE", "model_type": "ML"}

    log_audit(db, current_user.id, "INFERENCE_ML", "InferenceQueue", task_id)
    return {"task_id": task_id, "status": "PENDING", "model_type": "ML"}


@router.post("/dl")
async def run_dl_inference(
    body: InferenceDLRequest,
    current_user: User = Depends(require_medico_or_admin),
    db: Session = Depends(get_db),
):
    """Lanzar inferencia DL de imagen para un paciente."""
    patient = db.query(Patient).filter(
        Patient.id == body.patient_id,
        Patient.deleted_at.is_(None),
    ).first()
    if not patient:
        raise HTTPException(404, "Paciente no encontrado")

    task_id = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{ORCHESTRATOR_URL}/infer", json={
                "patient_id": str(body.patient_id),
                "model_type": "DL",
                "image_url": body.image_url,
            })
            resp.raise_for_status()
            result = resp.json()
            task_id = result["task_id"]
    except Exception:
        dl_result = _simulate_dl(str(body.patient_id))
        _local_tasks[task_id] = {
            "task_id": task_id,
            "status": "DONE",
            "model_type": "DL",
            "result": dl_result,
        }
        report = RiskReport(
            patient_id=body.patient_id,
            model_type="DL",
            risk_score=dl_result["probability"],
            risk_category=dl_result["risk_category"],
            risk_prediction=dl_result["risk_prediction"],
            gradcam_url=dl_result.get("gradcam_url"),
        )
        db.add(report)
        log_audit(db, current_user.id, "INFERENCE_DL", "RiskReport", str(report.id))
        return {"task_id": task_id, "status": "DONE", "model_type": "DL"}

    log_audit(db, current_user.id, "INFERENCE_DL", "InferenceQueue", task_id)
    return {"task_id": task_id, "status": "PENDING", "model_type": "DL"}


@router.get("/status/{task_id}")
async def get_inference_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consultar estado de una inferencia. Si está DONE, crea el RiskReport."""
    # Primero verificar local
    if task_id in _local_tasks:
        return _local_tasks[task_id]

    # Intentar orquestador
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ORCHESTRATOR_URL}/status/{task_id}")
            resp.raise_for_status()
            task_data = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, "Task no encontrada")
        raise HTTPException(503, "Orquestador no disponible")
    except Exception:
        raise HTTPException(503, "Orquestador no disponible")

    # Si está DONE, crear RiskReport
    if task_data["status"] == "DONE" and task_data.get("result"):
        result = task_data["result"]
        patient_id = task_data.get("patient_id")
        model_type = task_data.get("model_type", "ML")

        if patient_id:
            existing = db.query(RiskReport).filter(
                RiskReport.patient_id == patient_id,
                RiskReport.model_type == model_type,
                RiskReport.deleted_at.is_(None),
            ).first()

            if not existing:
                report = RiskReport(
                    patient_id=patient_id,
                    model_type=model_type,
                    risk_score=result.get("probability") or result.get("risk_score"),
                    risk_category=result.get("risk_category", "MEDIUM"),
                    risk_prediction=result.get("risk_prediction"),
                    shap_values=result.get("shap_values"),
                    gradcam_url=result.get("gradcam_url"),
                )
                db.add(report)
                log_audit(db, current_user.id, "CREATE_RISK_REPORT", "RiskReport", str(report.id))
                db.commit()

    return task_data
