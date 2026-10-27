// The sky behind every page: deep black space, rendered by one raw-WebGL fragment shader (no
// three.js). Three layers of twinkling stars drift slowly at different speeds, over a faint
// Milky Way band with dark dust lanes. It is data-driven:
//   - every real request.end event makes the galaxy glow a little, briefly;
//   - the screen edge warms to amber when the gateway is degraded and red when a breaker opens.
// ~0.75x resolution, ~30 fps, paused when the tab is hidden. Reduced motion draws one still frame.
import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useSystemMood } from "../HoverRobot";
import { onEvent } from "../../lib/live";
import { useReducedMotion } from "../../lib/motion";

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `
precision highp float;
uniform vec2 res;
uniform float t;
uniform float pulse;
uniform float alarm;
uniform float warn;
uniform float gain;

float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p){
  vec2 i = floor(p); vec2 f = fract(p); f = f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y);
}
float fbm(vec2 p){ float v = 0.0; float a = 0.5; for (int i = 0; i < 5; i++){ v += a*noise(p); p *= 2.03; a *= 0.5; } return v; }

// one layer of stars: at most one per cell, each twinkling at its own rate
vec3 stars(vec2 uv, float scale, float density, float size){
  vec2 g = uv * scale; vec2 id = floor(g); vec2 f = fract(g) - 0.5;
  float h = hash(id);
  if (h > density) return vec3(0.0);
  vec2 off = (vec2(hash(id + 3.1), hash(id + 7.7)) - 0.5) * 0.6;
  float d = length(f - off);
  float tw = 0.55 + 0.45 * sin(t * (0.6 + h * 2.4) + h * 60.0);
  float core = exp(-d * d * size) * tw;
  vec2 a = abs(f - off);
  float glint = (max(0.0, 1.0 - a.x * 40.0) * max(0.0, 1.0 - a.y * 4.0) + max(0.0, 1.0 - a.y * 40.0) * max(0.0, 1.0 - a.x * 4.0))
              * step(0.985, hash(id + 9.0)) * tw;
  vec3 tint = mix(vec3(0.75, 0.85, 1.0), vec3(1.0, 0.95, 0.88), hash(id + 1.7));
  return tint * (core + glint * 0.18);
}

void main(){
  vec2 uv = gl_FragCoord.xy / res;
  vec2 q = (gl_FragCoord.xy - 0.5 * res) / res.y;

  // the Milky Way: a soft diagonal band cut by dark dust lanes
  vec2 n = normalize(vec2(-0.55, 1.0));
  float d = dot(q, n) + 0.08 * sin(q.x * 1.3 + 0.6);
  float band = exp(-d * d * 9.0);
  vec2 bq = q * 2.2 + vec2(t * 0.004, 0.0);
  float dust = smoothstep(0.35, 0.75, fbm(bq * 2.0 + 5.0));
  float glow = fbm(bq + 1.3);
  vec3 milky = mix(vec3(0.34, 0.42, 0.66), vec3(0.78, 0.80, 0.95), glow) * band * (0.55 + 0.6 * glow) * (1.0 - 0.75 * dust);

  vec3 col = vec3(0.0);
  col += milky * 0.17 * (1.0 + pulse * 0.9) * gain;
  col += vec3(0.02, 0.03, 0.07) * fbm(q * 1.4 - t * 0.003) * gain;

  // stars at three depths, drifting at different speeds (parallax), plus a dense dust in the band
  col += stars(q + vec2(t * 0.0015, 0.0), 90.0, 0.32, 900.0) * 0.55;
  col += stars(q + vec2(t * 0.003, t * 0.0008), 42.0, 0.22, 700.0) * 0.85;
  col += stars(q + vec2(t * 0.006, t * 0.0015), 18.0, 0.12, 500.0) * (1.1 + pulse * 0.6);
  col += stars(q + vec2(17.0), 160.0, 0.5 * band, 1400.0) * 0.6 * gain;

  // state: the edges warm when degraded and burn red while a breaker is open
  float edge = smoothstep(0.35, 0.95, length(uv - 0.5) * 1.35);
  col += vec3(0.9, 0.55, 0.15) * edge * warn * 0.18;
  col += vec3(1.0, 0.18, 0.25) * edge * alarm * 0.26;

  gl_FragColor = vec4(col, 1.0);
}`;

export default function Aurora() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const mood = useSystemMood();
  const reduced = useReducedMotion();
  const { pathname } = useLocation();
  const gainTarget = useRef(1);
  gainTarget.current = pathname === "/" ? 1 : 0.75;
  const target = useRef({ alarm: 0, warn: 0 });
  target.current = { alarm: mood === "alarmed" ? 1 : 0, warn: mood === "concerned" ? 1 : 0 };

  useEffect(() => {
    const c = canvas.current;
    const gl = c?.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
    if (!c || !gl) return;
    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.error("sky shader:", gl.getShaderInfoLog(s));
      return s;
    };
    const prog = gl.createProgram()!;
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const u = (n: string) => gl.getUniformLocation(prog, n);
    const uRes = u("res"), uT = u("t"), uPulse = u("pulse"), uAlarm = u("alarm"), uWarn = u("warn"), uGain = u("gain");

    const scale = Math.min(1, 0.75 * (window.devicePixelRatio || 1));
    const resize = () => {
      c.width = Math.max(1, Math.floor(window.innerWidth * scale));
      c.height = Math.max(1, Math.floor(window.innerHeight * scale));
      gl.viewport(0, 0, c.width, c.height);
    };
    resize();
    window.addEventListener("resize", resize);

    let pulse = 0;
    const off = onEvent((e) => {
      if (e.type === "request.end") pulse = Math.min(1.4, pulse + 0.6);
    });

    let alarm = 0, warn = 0, gain = gainTarget.current;
    let raf = 0;
    let last = 0;
    const start = performance.now();
    const draw = (now: number, dt: number) => {
      alarm += (target.current.alarm - alarm) * Math.min(1, dt * 2);
      warn += (target.current.warn - warn) * Math.min(1, dt * 2);
      gain += (gainTarget.current - gain) * Math.min(1, dt * 1.5);
      pulse *= Math.pow(0.25, dt);
      gl.uniform2f(uRes, c.width, c.height);
      gl.uniform1f(uT, (now - start) / 1000 + 40);
      gl.uniform1f(uPulse, pulse);
      gl.uniform1f(uAlarm, alarm);
      gl.uniform1f(uWarn, warn);
      gl.uniform1f(uGain, gain);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (document.hidden || now - last < 33) return;
      const dt = last ? (now - last) / 1000 : 0.033;
      last = now;
      draw(now, dt);
    };
    if (reduced) {
      alarm = target.current.alarm;
      warn = target.current.warn;
      draw(performance.now(), 1);
    } else {
      raf = requestAnimationFrame(frame);
    }
    return () => {
      cancelAnimationFrame(raf);
      off();
      window.removeEventListener("resize", resize);
    };
  }, [reduced]);

  return <canvas ref={canvas} aria-hidden="true" className="pointer-events-none fixed inset-0 -z-20 h-full w-full" />;
}
