struct MyTy<'a>(Vec<u8>, &'a ());

impl MyTy<'_> {
    fn one(&mut self) -> &mut impl Sized {
        &mut self.0
    }
    fn two(&mut self) -> &mut (impl Sized + 'static) {
        self.one()
        //~^ ERROR lifetime may not live long enough
    }
}
fn main() {}