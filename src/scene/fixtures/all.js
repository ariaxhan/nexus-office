/**
 * Every fixture in the room, listed once.
 *
 * A fixture is one thing on the floor or the wall that is not a desk: the clock,
 * the cost chart, the mailroom. Each owns exactly one file, which is what lets
 * several of them be built at once without anyone stepping on anyone.
 *
 * A plain array rather than a directory scan. Seven lines you can read beat a
 * glob you have to reason about, and this is the only place a fixture is
 * registered.
 *
 * A fixture module exports:
 *
 *   id      string, unique. Also the key its data lives under in world.sections
 *   title   what the panel is called
 *   wall    true if it wants a slot on the back wall (see office.js)
 *   build(ctx) -> THREE.Object3D | null
 *           null means "nothing to draw", which is allowed and must be honest:
 *           a fixture with no data draws its own absence rather than vanishing.
 *   panel(payload, world, api) -> Node
 *           api = { el, md, queue, toast, refresh }
 *   demo() -> the section shape, for ?demo=1
 *
 * ctx = { world, section, stations, room }
 *   section   world.sections[id], or undefined
 *   stations  the desks as placed, each carrying x and z
 *   room      { minX, maxX, minZ, maxZ, centre, wall }
 *   room.wall this fixture's slot: { x, z, w, h }, facing +z. Null if wall:false
 *
 * Anything a fixture wants clickable carries
 *   mesh.userData.fixture = { id, payload }
 * and office.js picks it up.
 */
import * as board from "./board.js";
import * as ci from "./ci.js";
import * as clock from "./clock.js";
import * as cost from "./cost.js";
import * as intray from "./intray.js";
import * as journey from "./journey.js";
import * as library from "./library.js";
import * as mail from "./mail.js";
import * as orders from "./orders.js";

export const FIXTURES = [intray, ci, journey, orders, mail, board, cost, library, clock];

export const byId = (id) => FIXTURES.find((f) => f.id === id);
