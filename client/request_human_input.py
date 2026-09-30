"""Worker entrypoint for the sole human-input request path."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import human_asks


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--type',required=True,choices=sorted(human_asks.GATE_TYPES))
    parser.add_argument('--action',required=True)
    parser.add_argument('--why',required=True)
    parser.add_argument('--authorization-gap',required=True)
    parser.add_argument('--resume',required=True)
    args=parser.parse_args(argv)
    task=os.environ.get('OFFICE_TASK_ID')
    if not task:
        parser.error('OFFICE_TASK_ID is required')
    with human_asks.connect() as db:
        identifier=human_asks.request_human_input(
            db,identifier='office-task:'+task,execution_ref=task,
            gate_type=args.type,action=args.action,
            why_agent_cannot_do_it=args.why,
            authorization_gap=args.authorization_gap,
            resume_after_answer=args.resume)
    print(identifier)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
