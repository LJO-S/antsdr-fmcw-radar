// K9 turntable: HMC849A SPDT switch picks which antenna column the VNA feeds.
// Procedure: scripts/antenna_design/k9_turntable.md
//
// Wiring (Arduino Nano, 5 V):
//   VDD    3.3 V bench supply
//   GND    supply minus + Arduino GND (common)
//   EN     GND
//   V_CTL  D2 -> 1 k -> V_CTL, V_CTL -> 2 k -> GND (3.33 V high)
//
// A = RF2 = V_CTL low (boot default). B = RF1 = V_CTL high. LED on = B.
// Serial 115200: a, b, ? (report only).
//
// Power order: supply on, then Arduino; off in reverse. V_CTL must never
// exceed VDD + 1 V.
// Opening the serial port resets the Nano, which returns the switch to A.

const uint8_t CTL_PIN = 2;

bool sel_b = false;

void apply(bool b) {
  sel_b = b;
  digitalWrite(CTL_PIN, b ? HIGH : LOW);
  digitalWrite(LED_BUILTIN, b ? HIGH : LOW);
}

void report() {
  Serial.println(sel_b ? F("B (RF1, V_CTL high)") : F("A (RF2, V_CTL low)"));
}

void setup() {
  digitalWrite(CTL_PIN, LOW);  // first: no high glitch on V_CTL
  pinMode(CTL_PIN, OUTPUT);
  pinMode(LED_BUILTIN, OUTPUT);
  apply(false);
  Serial.begin(115200);
  report();
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    switch (c) {
      case 'a': case 'A': apply(false); report(); break;
      case 'b': case 'B': apply(true);  report(); break;
      case '?': report(); break;
      case '\r': case '\n': case ' ': case '\t': break;
      default: Serial.println(F("commands: a = A (RF2), b = B (RF1), ? = report")); break;
    }
  }
}
