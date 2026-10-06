// crate dep
trait IsUnit {}
impl IsUnit for () {}


pub trait WithAssoc<'a> {
    type Assoc;
}

// The two impls of `Trait` overlap
pub trait Trait {}
impl<T> Trait for T
where
    T: 'static,
    for<'a> T: WithAssoc<'a>,
    for<'a> <T as WithAssoc<'a>>::Assoc: IsUnit,
{
}
impl<T> Trait for Box<T> {}