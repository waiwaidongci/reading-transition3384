import json,random,logging
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.settings import CONFIG
from src.models.user import User
from src.models.source import Source
from src.models.record import Record
from src.models.run import Run
from src.models import note,audit
from src.utils.security import hash_password
from src.utils.serialization import now
from src.services.runs import queue
from src.services.domain_validation import validate_record

# 三个文集的章节词表互有重叠，另有 3 条路线被三个文集共用。
# 共用路线让跨文集分组出现，词表内共享连续三章的路线让间接相连的会话合并成组。
CHAPTER_THEMES=[
 ['序章','材料','工艺','案例','附录','索引','访谈','补记','年表','译注','档案','展览','丛书','手记','校勘','版本','书评','插图','版式','用纸','油墨','折页','裁切','锁线','胶装','护封','扉页','版权','致谢','注释','后记','凡例'],
 ['导言','总论','选材','打样','校色','制版','上机','折手','配页','压平','切边','包封','烫印','压纹','覆膜','模切','裱糊','装函','编号','入库','抽检','复核','归档','留样','套准','过油','压痕','拼版','晒版','显影'],
 ['卷首','图版','图注','释文','勘误','引文','谱系','手稿','批注','题跋','钤印','装池','补纸','托底','镶边','挖补','压条','衬绢','覆背','上墙','砑光','裁齐','包角','订线','揭裱','隔水','引首','拖尾'],
]
SHARED_ROUTES=[['检字','排版','付印'],['选纸','试印','定样'],['校样','改版','复核']]
_PATH_CACHE={}

def transition_paths(batch):
    """每个文集固定 30 条路线（3 条跨文集共用 + 27 条本地），各出现 8 次。"""
    if batch in _PATH_CACHE:return _PATH_CACHE[batch]
    vocab=CHAPTER_THEMES[batch%len(CHAPTER_THEMES)];local=[];cursor=0
    while len(local)<27 and cursor<4000:
        size=[3,4,2,3,4][cursor%5];start=(cursor*5)%(len(vocab)-size)
        route=vocab[start:start+size]
        if route not in local:local.append(list(route))
        cursor+=1
    if len(local)!=27:raise RuntimeError('演示路线生成失败')
    routes=SHARED_ROUTES+local
    paths=[]
    for route in routes:paths.extend([list(route)]*8)
    random.Random(1000+batch).shuffle(paths)
    _PATH_CACHE[batch]=paths
    return paths

def seed_values(kind,index,batch,rng):
    if kind=='gaussian':
        cls=index%3;return {'rms':round(max(.1,rng.gauss([1.2,3.0,2.0][cls]+batch*.08,.35)),3),'centroid':round(max(100,rng.gauss([900,1600,2300][cls],170)),2),'kurtosis':round(max(.1,rng.gauss([2.8,5.2,3.9][cls],.45)),3),'label':['正常','轴承磨损','转子偏心'][cls]}
    if kind=='density':
        cls=index%3
        if index%19==0:return {'temperature':round(rng.uniform(12,42),2),'humidity':round(rng.uniform(12,92),2),'particles':round(rng.uniform(4,140),2)}
        return {'temperature':round(rng.gauss([19,26,32][cls]+batch*.3,.8),2),'humidity':round(rng.gauss([72,45,28][cls],2.5),2),'particles':round(max(.1,rng.gauss([22,48,85][cls],3)),2)}
    if kind=='transition':
        path=transition_paths(batch)[index];return {'session':f'SY-{batch+1}-{index+1:04}','path':'>'.join(path)}
    if kind=='forest':
        moisture=rng.uniform(20,60);thickness=rng.uniform(1,8);temperature=rng.uniform(50,100);airflow=rng.uniform(1,5)
        minutes=12+1.6*moisture+8*thickness-.45*temperature-3*airflow+0.1*moisture*thickness+rng.gauss(0,3)
        return {'moisture':round(moisture,2),'thickness':round(thickness,2),'temperature':round(temperature,2),'airflow':round(airflow,2),'minutes':round(max(5,minutes),2)}
    if kind=='neighbors':
        uid=index//6;slot=index%6;exhibit=(uid%3*3+slot)%12
        return {'visitor':f'V{uid+1:03}','exhibit':f'E{exhibit+1:02}','rating':round(max(1,min(5,3+(1 if exhibit%3==uid%3 else -.4)+rng.gauss(0,.55))),1)}

def counts(db):
    return {cls.__tablename__:db.scalar(select(func.count()).select_from(cls)) for cls in (User,Source,Record,Run)}
def seed():
    Base.metadata.create_all(engine);rng=random.Random(42);queued=[]
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@'+CONFIG['slug']+'.demo'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password_hash=hash_password('Research2026!'),role=role))
        for batch in range(3):
            key=CONFIG['code']+f'-202611-{batch+1:02}'
            source=db.scalar(select(Source).where(Source.key==key))
            if not source:
                source=Source(key=key,name=key+' '+['秋季常规组','复测对照组','工艺观察组'][batch],filename=CONFIG['slug']+f'-202611-{batch+1:02}.csv',description=CONFIG['scenario']);db.add(source);db.flush()
            for index in range(CONFIG['seed_count']):
                reference=key+f'-{index+1:04}'
                vals=seed_values(CONFIG['algorithm'],index,batch,rng)
                if not db.scalar(select(Record.id).where(Record.source_id==source.id,Record.reference==reference)):
                    db.add(Record(source_id=source.id,reference=reference,values=validate_record(vals)))
            db.flush()
            if batch==0 and not db.scalar(select(Run.id).where(Run.name=='首轮种子研究')):
                records=[{'id':r.id,'source_id':r.source_id,'reference':r.reference,'values':r.values} for r in db.scalars(select(Record).where(Record.source_id==source.id).order_by(Record.id))]
                run=Run(source_id=source.id,name='首轮种子研究',state='queued',parameters=CONFIG['default_parameters'],snapshot={'source':{'id':source.id,'name':source.name,'filename':source.filename},'records':records},created_at=now());db.add(run);db.flush();queued.append(run.id)
        db.commit();print(json.dumps(counts(db),ensure_ascii=False))
    for id in queued:
        try:queue.rpush('analysis:queue',id)
        except Exception:logging.exception('seed queue unavailable')
if __name__=='__main__':seed()
