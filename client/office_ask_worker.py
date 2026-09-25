"""Independent owner of the durable Office Ask queue."""
import fcntl
import sys

import office_ask


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--check':
        return 0 if office_ask.worker_health() else 1
    if len(sys.argv) != 1:
        return 2
    lock_path = office_ask.path().with_suffix('.worker.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 3
        office_ask.worker_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
