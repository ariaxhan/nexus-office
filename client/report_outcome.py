"""Record verified completion of an Office task's persisted parent outcome."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus.ledger import Ledger
from nexus import office_tasks, terminal


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',action='append',required=True,
                        help='One concrete production or target-behavior verification receipt')
    args=parser.parse_args(argv)
    task=os.environ.get('OFFICE_TASK_ID')
    ledger_path=os.environ.get('OFFICE_NEXUS_LEDGER')
    if not task or not ledger_path:
        parser.error('an Office task and ledger are required')
    with closing(Ledger(ledger_path)) as ledger:
        spec=office_tasks.specification(ledger,task)
        terminal_condition=spec.get('outcome_contract',{}).get('terminal_condition') or spec['prompt']
        evidence=[item.strip() for item in args.evidence if item.strip()]
        if not evidence:
            raise ValueError('concrete verification evidence is required')
        ledger.set_task_state(task,'done',decided_by='office',
                              reason='terminal outcome verified',
                              evidence=terminal.delivered({'verified':True,'evidence':evidence}))
        ledger.event('office.outcome_verified',task,
                     {'terminal_condition':terminal_condition,
                      'evidence':evidence},source='office-engine')
    print('DONE')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
