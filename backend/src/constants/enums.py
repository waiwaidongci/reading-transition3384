from enum import Enum
class Role(str,Enum):
    admin='admin'; analyst='analyst'; viewer='viewer'
class RunState(str,Enum):
    queued='queued'; running='running'; completed='completed'; failed='failed'
class SplitState(str,Enum):
    queued='queued'; running='running'; pending='pending'; completed='completed'; infeasible='infeasible'; failed='failed'
class ReviewState(str,Enum):
    draft='draft'; approved='approved'
