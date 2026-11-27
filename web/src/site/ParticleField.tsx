/**
 * The landing page backdrop, built on the licensed Peachworlds "Vexel AI" scene (see
 * public/scene/LICENSE.txt): a ring of 32 spinning tori cut into glowing dashes by an alpha map,
 * lit by two cyan point lights that travel with it, under bloom. The camera stays fixed.
 *
 * The choreography is ours. Each section (`data-scene`) has a shot: a pose for the model, plus how
 * strongly it blooms, how fast it spins and how much the page UI glows with it. Pinned sections
 * get a start and an end shot, keyed to their real pin range, so the model follows the workflow
 * steps and the sliding capabilities exactly. Between shots everything is interpolated and damped.
 *
 * Loaded lazily, only in rich motion mode with WebGL2; everything else keeps the static glow.
 */
import { useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { BloomEffect, EffectComposer, EffectPass, HueSaturationEffect, RenderPass, ToneMappingEffect, ToneMappingMode } from 'postprocessing'
import { markSceneReady, sceneState } from './sceneState'
import styles from './ParticleField.module.css'

const MODEL_URL = '/scene/model.glb'
const ALPHA_URL = '/scene/alpha-map.png'

type Vec3 = [number, number, number]
interface Shot {
  pos: Vec3
  rot: Vec3
  /** BloomEffect intensity. */
  bloom: number
  /** Spin speed of the tori (animation time scale). */
  spin: number
  /** How strongly the page UI glows with the scene (0..1), as --ak-scene-glow. */
  glow: number
}

const HALF_PI = Math.PI / 2
/** The template's dome-from-above pose: a vortex of dash rings under the copy. */
const DOME: Pick<Shot, 'pos' | 'rot'> = { pos: [0, -0.8727, 3.9964], rot: [-HALF_PI, 0, 0] }

/** Shots by section. Pinned sections (`pins`) have a start and an end shot. */
const SHOTS: Record<string, Shot | [Shot, Shot]> = {
  // Arrival: the glowing vortex under the headline.
  hero: { ...DOME, bloom: 1.5, spin: 0.5, glow: 1 },
  // Tension: the light lifts away and the problem sits in near-black.
  problem: { pos: [0, 3.2648, 3.9964], rot: [-HALF_PI, 0, 0], bloom: 0.4, spin: 0.15, glow: 0.15 },
  // Spotlight cards (one station per row): the model stays lifted away and dim, so the wireframe
  // illustrations carry the glow.
  spotlight: { pos: [0, 3.2648, 3.9964], rot: [-HALF_PI, 0, 0], bloom: 0.4, spin: 0.15, glow: 0.15 },
  // The fix is built step by step: the rings turn a quarter and brighten until the pull request.
  workflow: [
    { pos: [1.4, 0, 3.9863], rot: [0, 1.5681, 0], bloom: 0.6, spin: 0.25, glow: 0.35 },
    { pos: [1.4, 0, 3.9863], rot: [0, 1.5681 + HALF_PI, 0], bloom: 1.6, spin: 0.6, glow: 0.9 },
  ],
  // Breadth: an edge-on ribbon drifting along the right edge as the cards slide past it, clear of
  // the centred heading.
  capabilities: [
    { pos: [3.0, -0.3, 4.6503], rot: [0, 0, 0], bloom: 1, spin: 0.5, glow: 0.6 },
    { pos: [2.1, -0.3, 4.6503], rot: [0, 0.5, 0], bloom: 1.1, spin: 0.55, glow: 0.65 },
  ],
  // Containment: quiet and slow, off to the right behind the sandbox command.
  security: { pos: [2.1136, 0, 4.6503], rot: [0, 0.6, 0], bloom: 0.7, spin: 0.2, glow: 0.4 },
  // Bridge back toward the centre: the vortex rises from below, under the cards, toward the finale.
  integrations: { pos: [0, -1.9, 3.6], rot: [-1.2, 0, 0], bloom: 0.9, spin: 0.4, glow: 0.5 },
  // Resolution: the hero's vortex returns, at full bloom, behind the closing line.
  cta: { pos: [0, -1.35, 3.9964], rot: DOME.rot, bloom: 1.8, spin: 0.5, glow: 1 },
}

/** A shot and the scroll position (px) at which it is fully in place. */
interface Station {
  y: number
  shot: Shot
}

/** Where each shot sits on the page right now. Unpinned sections: when centred in the viewport. */
function readStations(): Station[] {
  const stations: Station[] = []
  for (const el of document.querySelectorAll<HTMLElement>('[data-scene]')) {
    const shot = SHOTS[el.dataset.scene ?? '']
    if (!shot) continue
    const pin = sceneState.pins[el.dataset.scene ?? '']
    if (Array.isArray(shot) && pin) {
      stations.push({ y: pin.start, shot: shot[0] }, { y: pin.end, shot: shot[1] })
    } else {
      const rect = el.getBoundingClientRect()
      const y = rect.top + window.scrollY + rect.height / 2 - window.innerHeight / 2
      stations.push({ y, shot: Array.isArray(shot) ? shot[0] : shot })
    }
  }
  return stations.sort((a, b) => a.y - b.y)
}

const ease = (t: number) => t * t * (3 - 2 * t)
const mix = (a: number, b: number, t: number) => a + (b - a) * t

/** The shot for the current scroll position, eased between neighbouring stations. */
function sample(out: Shot) {
  const stations = readStations()
  const first = stations[0]
  if (!first) return
  const y = window.scrollY
  let a = first
  let b = first
  for (const station of stations) {
    if (station.y <= y) a = station
    if (station.y >= y) {
      b = station
      break
    }
    b = station
  }
  const t = b.y > a.y ? ease(THREE.MathUtils.clamp((y - a.y) / (b.y - a.y), 0, 1)) : 0
  for (let k = 0; k < 3; k++) {
    out.pos[k] = mix(a.shot.pos[k], b.shot.pos[k], t)
    out.rot[k] = mix(a.shot.rot[k], b.shot.rot[k], t)
  }
  out.bloom = mix(a.shot.bloom, b.shot.bloom, t)
  out.spin = mix(a.shot.spin, b.shot.spin, t)
  out.glow = mix(a.shot.glow, b.shot.glow, t)
}

/** Written by the model each frame, read by the effects: the live bloom intensity. */
const live = { bloom: 0 }

/** Seconds for the scene to fade up from black after it loads. */
const INTRO_SECONDS = 1.2

/**
 * Emission at full strength. Brighter than the template's 1: our pipeline otherwise lands the
 * dashes below the bloom threshold, and the white-cored glow and blue haze come from that bloom.
 */
const EMISSIVE = 2.2

function Model() {
  const gltf = useLoader(GLTFLoader, MODEL_URL)
  const alphaMap = useLoader(THREE.TextureLoader, ALPHA_URL)
  const maxAnisotropy = useThree((s) => s.gl.capabilities.getMaxAnisotropy())
  const group = useRef<THREE.Group>(null)
  const lights = useRef<(THREE.PointLight | null)[]>([])
  const material = useRef<THREE.MeshPhysicalMaterial | null>(null)
  const target = useMemo<Shot>(() => ({ pos: [...DOME.pos], rot: [...DOME.rot], bloom: 1.5, spin: 0.5, glow: 1 }), [])
  const current = useMemo(
    () => ({ pos: new THREE.Vector3(...DOME.pos), rot: new THREE.Vector3(...DOME.rot), bloom: 1.5, spin: 0.5, glow: 1, intro: 0, lastGlow: -1 }),
    [],
  )

  // One shared material, as configured in the template: the GLB's soft round base texture limits
  // each torus to an arc, and the dash alpha map cuts that arc into dashes.
  const model = useMemo(() => {
    // Sample the dash mask without mipmaps, so the alpha cut keeps thin, crisp dashes instead of
    // blurred blobs; anisotropy keeps them clean at grazing angles.
    alphaMap.generateMipmaps = false
    alphaMap.minFilter = THREE.LinearFilter
    alphaMap.anisotropy = maxAnisotropy
    let map: THREE.Texture | null = null
    gltf.scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh && !map) map = ((o as THREE.Mesh).material as THREE.MeshStandardMaterial).map
    })
    const material = new THREE.MeshPhysicalMaterial({
      color: '#006cff',
      map,
      alphaMap,
      alphaTest: 0.1,
      metalness: 1,
      roughness: 1,
      emissive: '#65a5ff',
      emissiveIntensity: 0, // faded up to EMISSIVE on load
      ior: 1.5,
      reflectivity: 0.5,
      specularIntensity: 0,
      side: THREE.FrontSide,
    })
    gltf.scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).material = material
    })
    return { scene: gltf.scene, material }
  }, [gltf, alphaMap, maxAnisotropy])
  // Read the material back from the meshes: under StrictMode the memo above can run twice, and only
  // the meshes know which material instance actually renders.
  useEffect(() => {
    const mesh = model.scene.getObjectByProperty('isMesh', true) as THREE.Mesh | undefined
    material.current = (mesh?.material as THREE.MeshPhysicalMaterial | undefined) ?? null
  }, [model])

  // Compile shaders before the first visible frame (no hitch as it fades up), then tell the page:
  // the hero's entrance waits for this, so copy and scene arrive together.
  const { gl, scene, camera } = useThree()
  useEffect(() => {
    gl.compile(scene, camera)
    markSceneReady()
  }, [gl, scene, camera, model])

  // The tori spin on the model's own clip, looping at half speed. Created in an effect so that
  // creation and disposal pair up under React's StrictMode remounts.
  const mixer = useRef<THREE.AnimationMixer | null>(null)
  useEffect(() => {
    const m = new THREE.AnimationMixer(model.scene)
    const clip = gltf.animations[0]
    if (clip) m.clipAction(clip).setLoop(THREE.LoopRepeat, Infinity).play()
    m.timeScale = 0.5
    mixer.current = m
    return () => {
      m.stopAllAction()
      mixer.current = null
    }
  }, [gltf, model])

  useFrame((_, delta) => {
    sample(target)
    const k = 1 - Math.exp(-5 * delta)
    current.pos.lerp(new THREE.Vector3(...target.pos), k)
    current.rot.lerp(new THREE.Vector3(...target.rot), k)
    current.bloom = mix(current.bloom, target.bloom, k)
    current.spin = mix(current.spin, target.spin, k)
    current.glow = mix(current.glow, target.glow, k)
    // Fade up from black on load: light, emission and bloom rise together.
    current.intro = Math.min(1, current.intro + delta / INTRO_SECONDS)
    const intro = ease(current.intro)

    if (mixer.current) {
      mixer.current.timeScale = current.spin
      mixer.current.update(delta)
    }
    group.current?.position.copy(current.pos)
    group.current?.rotation.set(current.rot.x, current.rot.y, current.rot.z)
    if (material.current) material.current.emissiveIntensity = EMISSIVE * intro
    for (const light of lights.current) if (light) light.intensity = 25 * intro
    live.bloom = current.bloom * intro

    const glow = current.glow * intro
    if (Math.abs(glow - current.lastGlow) > 0.01) {
      current.lastGlow = glow
      document.documentElement.style.setProperty('--ak-scene-glow', glow.toFixed(3))
    }
  })

  return (
    <group ref={group}>
      <primitive object={model.scene} scale={1.5} />
      <pointLight ref={(l) => { lights.current[0] = l }} color="#18caff" intensity={0} distance={20} decay={1.76} position={[-1.08, -0.9419, 1.2214]} />
      <pointLight ref={(l) => { lights.current[1] = l }} color="#00e7ff" intensity={0} distance={20} decay={1.76} position={[0.8664, 1.1824, 1.2214]} />
    </group>
  )
}

/** Bloom, hue/saturation and ACES tone mapping, as in the template's post-processing stack. */
function Effects() {
  const { gl, scene, camera, size } = useThree()
  const composer = useRef<EffectComposer | null>(null)
  const bloomEffect = useRef<BloomEffect | null>(null)
  useEffect(() => {
    const c = new EffectComposer(gl, { frameBufferType: THREE.HalfFloatType })
    c.addPass(new RenderPass(scene, camera))
    const bloom = new BloomEffect({ intensity: 0, luminanceThreshold: 0.9, luminanceSmoothing: 0.0285, radius: 0.75, mipmapBlur: true })
    bloomEffect.current = bloom
    c.addPass(
      new EffectPass(
        camera,
        bloom,
        // Hue after tone mapping, so rotated highlights stay white instead of turning pink.
        new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC }),
        new HueSaturationEffect({ hue: -0.2224, saturation: 0.21 }),
      ),
    )
    const v = gl.getSize(new THREE.Vector2())
    c.setSize(v.x, v.y)
    composer.current = c
    return () => {
      c.dispose()
      composer.current = null
    }
  }, [gl, scene, camera])
  useEffect(() => composer.current?.setSize(size.width, size.height), [size])
  // Takes over rendering (priority 1). Until the composer exists, draw the plain scene.
  useFrame((_, delta) => {
    if (!composer.current) return gl.render(scene, camera)
    if (bloomEffect.current) bloomEffect.current.intensity = live.bloom
    composer.current.render(delta)
  }, 1)
  return null
}

export default function ParticleField() {
  useEffect(() => () => {
    document.documentElement.style.removeProperty('--ak-scene-glow')
  }, [])
  return (
    <div className={styles.scene} aria-hidden="true">
      <Canvas
        flat
        dpr={[1, 1.5]}
        camera={{ position: [0, -0.0214, 8.3919], fov: 30, near: 0.1, far: 1000 }}
        gl={{ antialias: false, powerPreference: 'high-performance', stencil: false }}
      >
        <color attach="background" args={['#000000']} />
        <ambientLight intensity={2} />
        <Model />
        <Effects />
      </Canvas>
    </div>
  )
}
