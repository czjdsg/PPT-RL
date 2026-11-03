"""Linux process-safe inference leases, released by the OS on worker exit."""
import fcntl,os,threading,time
from pathlib import Path

class FlashGate:
    def __init__(self,directory,capacity=8,local_capacity=3):
        if capacity<1 or local_capacity<1:raise ValueError('Inference limits must be positive')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.capacity=capacity;self.local=threading.BoundedSemaphore(local_capacity);self.thread=threading.local()
    def __enter__(self):
        self.local.acquire()
        try:
            assert getattr(self.thread,'fd',None) is None
            while True:
                for i in range(self.capacity):
                    fd=os.open(self.directory/f'lease-{i:02d}.lock',os.O_CREAT|os.O_RDWR,0o600)
                    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:os.close(fd);continue
                    self.thread.fd=fd;return self
                time.sleep(.1)
        except BaseException:self.local.release();raise
    def __exit__(self,*args):
        fd=self.thread.fd
        try:fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd);self.thread.fd=None;self.local.release()
