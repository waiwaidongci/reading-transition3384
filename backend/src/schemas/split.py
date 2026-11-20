from pydantic import BaseModel,Field
class SplitInput(BaseModel):
    name:str=Field(min_length=2,max_length=160)
    collection_ids:list[int]=Field(min_length=1,max_length=20)
    budget_seconds:float=Field(default=20,gt=0,le=300)
