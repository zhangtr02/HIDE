#[derive(Clone, Eq)]
pub struct Struct<T>(T);

impl<T: Clone, U> PartialEq<U> for Struct<T>
where
U: Into<Struct<T>> + Clone
{
fn eq(&self, _other: &U) -> bool {
todo!()
}
}

fn main() {}