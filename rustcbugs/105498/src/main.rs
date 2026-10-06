#![feature(type_alias_impl_trait)]
trait Cap<'a> {}
impl<T> Cap<'_> for T {}

mod case1 {
    type MyStr<'x> = impl Sized + super::Cap<'x>;
    fn case1<'s>(s: &'s str) {
        || { let _: MyStr<'s> = s; };
        //~^ ERROR unconstrained opaque type
    }
}

mod case2 {
    type MyStr<'x> = impl Sized + super::Cap<'x> + 'x; // note `+ 'x`
    fn case2<'s>() {
        let _: MyStr<'s> = "";  // inferred ty: &'s str
        || -> MyStr<'s> { "" }; // inferred ty: &'static str
        //~^ ERROR concrete type differs from previous defining opaque type use
    }
}

mod case3 {
    type MyStr<'x> = impl Sized + super::Cap<'x>;
    fn case3<'s>() {
        |s: &'s str| { let _: MyStr<'s> = s; };
        //~^ ERROR captures lifetime that does not appear in bounds
    }
}

fn main() {}