use std::{
	error, fmt,
	io::{self, Cursor, Read, Write},
	marker::PhantomData,
	result,
};

struct S;

impl Read for S {
	fn read(&mut self, _buf: &mut [u8]) -> io::Result<usize> {
		todo!()
	}
}

impl Write for S {
	fn write(&mut self, _buf: &[u8]) -> io::Result<usize> {
		todo!()
	}

	fn flush(&mut self) -> io::Result<()> {
		todo!()
	}
}

pub type Result<T, E = Error> = result::Result<T, E>;
pub enum Error {}

struct Request;
struct Response;

pub trait Callback {}

impl<F> Callback for F where F: FnOnce(&Request, Response) -> Result<Response> {}

pub enum HandshakeError<Role: HandshakeRole> {
	Interrupted(MidHandshake<Role>),
}

impl<Role: HandshakeRole> fmt::Debug for HandshakeError<Role> {
	fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
		todo!()
	}
}

impl<Role: HandshakeRole> fmt::Display for HandshakeError<Role> {
	fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
		todo!()
	}
}

impl<Role: HandshakeRole> error::Error for HandshakeError<Role> {}

pub trait HandshakeRole {
	type InternalStream: Read + Write;
}

pub struct ServerHandshake<S, C> {
	_marker: PhantomData<(S, C)>,
}

impl<S: Read + Write, C: Callback> HandshakeRole for ServerHandshake<S, C> {
	type InternalStream = S;
}

pub struct MidHandshake<Role: HandshakeRole> {
	machine: HandshakeMachine<Role::InternalStream>,
}

impl<Role: HandshakeRole> MidHandshake<Role> {
	pub fn handshake(mut self) -> Result<(), HandshakeError<Role>> {
		todo!()
	}
}

pub struct HandshakeMachine<Stream> {
	stream: Stream,
	state: io::Cursor<Vec<u8>>,
}

fn accept<C: Callback>(callback: C) -> Result<(), HandshakeError<ServerHandshake<S, C>>> {
	todo!()
}

fn main() {
	accept(|_, _| {
		let callback = |_, _| todo!();

		accept(callback);
		todo!()
	});
}