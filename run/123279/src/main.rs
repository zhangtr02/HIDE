pub trait Job: AsJob {
    fn run_once(&self);
}

impl<F: Fn()> Job for F {
    fn run_once(&self) {
        todo!()
    }
}

pub trait AsJob {
}

impl<T: Job + Sized> AsJob for T { // <----- changing this to `Sized + Job` or just `Job` will FIX it.
}

pub struct LoopingJobService {
    job: Box<dyn Job>,
}

impl Job for LoopingJobService {
    fn run_once(&self) {
        self.job.run_once()
    }
}

fn main() {}