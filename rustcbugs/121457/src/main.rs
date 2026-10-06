pub trait Iterable {
    type Item<'a>
    where
        Self: 'a;

    fn iter(&self) -> impl '_;
}

impl<'a, I: 'a + Iterable> Iterable for &'a I {
    type Item<'a>: 'a;

    fn iter(&self) -> impl Iterator<Item = Self::Item<'missing>>;
}

fn main() {}