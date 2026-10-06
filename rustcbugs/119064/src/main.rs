use futures_core::future::BoxFuture;
use futures_util::FutureExt;
use tokio::sync::MutexGuard;
use futures_lite::future::block_on;

async fn run() {
    let m = tokio::sync::Mutex::new(());
    let _ = async move {
        let permit = ActionPermit { guard: m.lock().await };
        cancel_perform_async_boxed(permit).await;
    }
    .await;
}

fn main() {
    block_on(run())
}

async fn cancel_perform_async_boxed<T>(permit: ActionPermit<'_, T>) {
    let fut = permit.perform_async_boxed(|_| async move {}.boxed());
    tokio::select! {
        _ = fut => {}
    }
}

struct ActionPermit<'a, T> {
    guard: MutexGuard<'a, T>,
}

impl<'a, T> ActionPermit<'a, T> {
    async fn perform_async_boxed<R, F>(mut self, action: F) -> R
    where
        F: for<'lock> FnOnce(&'lock mut T) -> BoxFuture<'lock, R>,
    {
        action(&mut *self.guard).await
    }
}