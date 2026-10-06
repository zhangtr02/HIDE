#![allow(incomplete_features)]
#![feature(async_fn_in_trait)]

trait AsyncLendingIterator {
    type Item<'a>
    where
        Self: 'a;

    async fn next(&mut self) -> Option<Self::Item<'_>>;
}

struct Lend<I>(I);
impl<I> AsyncLendingIterator for Lend<I> {
    type Item<'a> = &'a I
    where
        Self: 'a;

    async fn next(&mut self) -> Option<Self::Item<'_>> {
        todo!()
    }
}

fn main() {}