use core::future::Future;

fn main() {
    do_async(
        async { (0,) },
        {
            // closure must be inside block
            |info| println!("{:?}", info.0)
        },
    );
}

fn do_async<R, Fut, F>(tokio_fut: Fut, glib_closure: F)
where
    Fut: Future<Output = R>,
    F: FnOnce(R),
{
}