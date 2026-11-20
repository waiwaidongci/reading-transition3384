from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.split import SplitInput
from ..services.auth import current_user,writer
from ..services.export import split_csv
from ..services.splits import detail,export_payload
from ..controllers.splits import SplitsController as C
router=APIRouter()
@router.get('/splits')
def splits(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/splits')
def create(data:SplitInput,db=Depends(db_session),u=Depends(writer)):return C.create(db,u,data)
@router.get('/splits/{id}')
def one(id:int,db=Depends(db_session),u=Depends(current_user)):return detail(db,id)
@router.post('/splits/{id}/resume')
def resume(id:int,db=Depends(db_session),u=Depends(writer)):return C.resume(db,u,id)
@router.get('/splits/{id}/export')
def export(id:int,db=Depends(db_session),u=Depends(current_user)):return split_csv(export_payload(db,id),'split-'+str(id)+'.csv')
