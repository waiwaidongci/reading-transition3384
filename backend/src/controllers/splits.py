from ..services import splits
class SplitsController:
    list=staticmethod(splits.list_jobs)
    create=staticmethod(splits.create_job)
    resume=staticmethod(splits.resume_job)
