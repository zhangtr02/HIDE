use futures::{Stream, StreamExt as _};

struct A<'a> {
    dat: &'a (),
}

impl<'a> A<'a> {
    async fn a(&self) -> impl Stream<Item = ()> {
        futures::stream::repeat(())
            .map(|()| futures::stream::repeat(()))
            .flatten_unordered(None)
    }
}