// Preloaded into the bench's Node-RED (node --require): shifts its clock by BSF_CLOCK_OFFSET_MS so the
// dispatcher's fixed 05:30-15:30 AEST window can be exercised at any hour. Timers still run in real time.
const OFF = +process.env.BSF_CLOCK_OFFSET_MS || 0;
if (OFF) {
  const RealDate = Date;
  class ShiftedDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(RealDate.now() + OFF); }
    static now() { return RealDate.now() + OFF; }
  }
  global.Date = ShiftedDate;
}
