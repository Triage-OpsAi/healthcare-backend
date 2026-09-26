import asyncio
import httpx
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import CurrentUser
from app.db.database import get_db
from app.schemas.documentation import SuggestionRequest, DraftCreate, DraftUpdate, RevisionRequest, ReviewRequest, FinalizeRequest
from app.services import documentation_service as service
from app.services.documentation_templates import TEMPLATES, suggest_template
from app.services.authorization import require_permission
from app.services.sarvam_service import transcribe_and_translate, SarvamAPIError

router=APIRouter(prefix="/ward-voice/documentation",tags=["Ward Voice"])
read=require_permission("emr:read")
write=require_permission("emr:create")
approve=require_permission("emr:review")

@router.get("/templates")
async def templates(user: CurrentUser=Depends(read)):
    return list(TEMPLATES.values())

@router.get("/patients/{patient_id}/context")
async def context(patient_id:uuid.UUID,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(read)):
    return await service.context(db,patient_id,user)

@router.get("/patients/{patient_id}")
async def documents(patient_id:uuid.UUID,response:Response,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(read)):
    response.headers["Cache-Control"]="no-store"
    result=await service.list_documents(db,patient_id,user)
    service.audit(db,user,"wardvoice_document.viewed","patient",patient_id,patient_id)
    await db.commit()
    return result

@router.post("")
async def create(payload:DraftCreate,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(write)):
    return await service.create(db,payload,user)

@router.put("/{doc_id}")
async def update(doc_id:uuid.UUID,payload:DraftUpdate,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(write)):
    return await service.update(db,doc_id,payload,user)

@router.post("/{doc_id}/generate")
async def generate(doc_id:uuid.UUID,payload:RevisionRequest,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(write)):
    return await service.generate(db,doc_id,payload,user)

@router.post("/{doc_id}/review")
async def review(doc_id:uuid.UUID,payload:ReviewRequest,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(approve)):
    return await service.review(db,doc_id,payload,user)

@router.post("/{doc_id}/finalize")
async def finalize(doc_id:uuid.UUID,payload:FinalizeRequest,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(approve)):
    return await service.finalize(db,doc_id,payload,user)

@router.post("/patients/{patient_id}/transcribe")
async def transcribe(patient_id:uuid.UUID,request:Request,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(write)):
    await service._patient(db,patient_id,user)
    await db.rollback()
    content_type=request.headers.get("content-type","").split(";")[0]
    extensions={"audio/webm":"webm","audio/ogg":"ogg","audio/mp4":"m4a","audio/wav":"wav","audio/mpeg":"mp3"}
    if content_type not in extensions: raise HTTPException(415,"Unsupported audio format")
    audio=bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio)>15*1024*1024: raise HTTPException(413,"Recording exceeds 15 MB. Use shorter recordings.")
    if len(audio)<100: raise HTTPException(422,"Recording is empty")
    try:
        result=await asyncio.wait_for(transcribe_and_translate(audio_bytes=bytes(audio),filename="dictation."+extensions[content_type],content_type=content_type),timeout=180)
    except (SarvamAPIError,TimeoutError,httpx.HTTPError): raise HTTPException(502,"Transcription is unavailable. Retry the recording or enter the transcript.")
    service.audit(db,user,"wardvoice_document.transcribed","patient",patient_id,patient_id)
    await db.commit()
    return result


@router.post("/suggest")
async def suggest_document(payload:SuggestionRequest,db:AsyncSession=Depends(get_db),user:CurrentUser=Depends(read)):
    ctx=await service.context(db,payload.patient_id,user)
    encounter=next((e for e in ctx["encounters"] if e["id"]==str(payload.encounter_id)),None)
    if not encounter: raise HTTPException(404,"Encounter not found for this patient")
    born=ctx["patient"]["date_of_birth"]
    age_days=(datetime.fromisoformat(encounter["date"])-datetime.fromisoformat(born)).days if born else None
    return suggest_template(encounter["department"],payload.transcript,age_days)
