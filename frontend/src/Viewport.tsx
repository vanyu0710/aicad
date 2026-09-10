import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { useT } from "./i18n";

export type AssemblyModel = {
  name: string;
  url: string;
  position?: number[] | null;
  rotationDeg?: [number, number[]] | null;
};

type Props = {
  objUrl?: string;
  stlUrl?: string;
  breadcrumb?: string;
  statusLabel?: string;
  // v0.14 F2a：装配预览——多零件按位姿叠加（提供 models 时优先于 stlUrl）
  models?: AssemblyModel[];
  hidden?: string[];
  selected?: string | null;
};

type ViewPreset = "iso" | "front" | "top" | "right" | "fit";
type ViewStatus =
  | "waiting"
  | "loading_obj"
  | "obj_loaded"
  | "obj_fallback"
  | "loading_stl"
  | "stl_loaded"
  | "load_failed";

// 装配分色板（按件轮换）：CAD 工程色，饱和度提高避免"发灰发粉"
const ASSEMBLY_COLORS = [0x9fb4c4, 0x4f86b8, 0xc99a4b, 0x59a06f, 0x8f6fb0, 0x4fa09a, 0xb06f6f, 0x6f7fb0];
// v0.14.1 渲染升级常量（首版 clearcoat+高 env 强度导致泛白洗色，收敛为扎实钢件）
const STEEL = { metalness: 0.32, roughness: 0.52, clearcoat: 0.12, envMapIntensity: 0.55 };
const EDGE_COLOR = 0xa8bfd4;
const EDGE_OPACITY = 0.35;
const EDGE_MAX_VERTICES = 300000; // 超过则跳过棱边线（47MB 齿轮 STL 的三角网会卡死）

export default function Viewport({ objUrl, stlUrl, breadcrumb, statusLabel, models, hidden, selected }: Props) {
  const t = useT();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const modelRef = useRef<THREE.Object3D | null>(null);
  const boundsRef = useRef<THREE.Box3 | null>(null);
  const [status, setStatus] = useState<ViewStatus>("waiting");
  const [viewMode, setViewMode] = useState<"shaded" | "wireframe">("shaded");
  const modelsKey = useMemo(() => JSON.stringify(models || []), [models]);
  const loaderKey = useMemo(() => `${objUrl || ""}:${stlUrl || ""}:${modelsKey}`, [objUrl, stlUrl, modelsKey]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) {
      return;
    }

    const width = Math.max(1, mount.clientWidth);
    const height = Math.max(1, mount.clientHeight);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a1c36);

    const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 4000);
    camera.position.set(110, 90, 110);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setClearColor(0x0a1c36, 1);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.95;
    mount.innerHTML = "";
    mount.appendChild(renderer.domElement);

    // v0.14.1 环境光照：RoomEnvironment 经 PMREM 生成反射环境，钢件质感的关键
    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTexture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTexture;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controlsRef.current = controls;

    // env 已提供环境填充光，直射光降为塑形主光 + 冷色轮廓光
    const ambient = new THREE.AmbientLight(0xdce6f0, 0.45);
    scene.add(ambient);

    const dir1 = new THREE.DirectionalLight(0xbfe6f5, 1.3);
    dir1.position.set(90, 140, 90);
    scene.add(dir1);

    const dir2 = new THREE.DirectionalLight(0x38c3e8, 0.55);
    dir2.position.set(-80, 60, -60);
    scene.add(dir2);

    const grid = new THREE.GridHelper(220, 22, 0x2c6f8c, 0x1c4a66);
    grid.material.opacity = 0.35;
    grid.material.transparent = true;
    scene.add(grid);

    scene.add(new THREE.AxesHelper(48));

    let disposed = false;
    let frameId = 0;

    const animate = () => {
      if (disposed) {
        return;
      }
      controls.update();
      renderer.render(scene, camera);
      frameId = window.requestAnimationFrame(animate);
    };
    animate();

    const fitCamera = (object: THREE.Object3D) => {
      const box = new THREE.Box3().setFromObject(object);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z, 20);
      const distance = maxDim * 1.9;
      boundsRef.current = box;
      camera.near = Math.max(0.1, maxDim / 100);
      camera.far = Math.max(2000, distance * 8);
      camera.updateProjectionMatrix();
      camera.position.set(center.x + distance, center.y + distance, center.z + distance);
      controls.target.copy(center);
      controls.update();
      camera.lookAt(center);
    };

    const applyWireframe = (root: THREE.Object3D, enabled: boolean) => {
      root.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (!mesh.isMesh) {
          return;
        }
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        materials.forEach((material) => {
          if (material && "wireframe" in material) {
            (material as THREE.MeshStandardMaterial).wireframe = enabled;
            material.needsUpdate = true;
          }
        });
      });
    };

    const clearModel = () => {
      if (modelRef.current) {
        scene.remove(modelRef.current);
        // v0.14.1 内存治理：释放 geometry/材质/边线，避免装配模式反复切换泄漏
        modelRef.current.traverse((child) => {
          const mesh = child as THREE.Mesh;
          if (mesh.geometry) {
            mesh.geometry.dispose();
          }
          if (mesh.material) {
            (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach((material) => material.dispose());
          }
        });
        modelRef.current = null;
      }
    };

    const makeSteelMaterial = (color: number) => {
      const material = new THREE.MeshPhysicalMaterial({ color, ...STEEL });
      material.wireframe = viewMode === "wireframe";
      return material;
    };

    // CAD 棱边线：STL 面片间夹角小（平面共面/曲面细密）会被阈值滤掉，
    // 只留真实特征棱线；超大网格跳过防卡顿。挂到 mesh.userData.edges 随显隐联动。
    const attachEdgeLines = (mesh: THREE.Mesh) => {
      const position = mesh.geometry?.getAttribute?.("position");
      if (!position || position.count > EDGE_MAX_VERTICES) {
        return;
      }
      try {
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(mesh.geometry, 24),
          new THREE.LineBasicMaterial({ color: EDGE_COLOR, transparent: true, opacity: EDGE_OPACITY }),
        );
        edges.userData.isEdgeHelper = true;
        mesh.add(edges);
        mesh.userData.edges = edges;
      } catch {
        // 边线失败不影响主体渲染
      }
    };

    const loadModel = async () => {
      clearModel();
      // v0.14 F2a 装配预览：逐件加载、按位姿摆放（不做 center()——会摧毁位姿）
      if (models && models.length) {
        setStatus("loading_stl");
        const group = new THREE.Group();
        try {
          for (let index = 0; index < models.length; index += 1) {
            const entry = models[index];
            const geometry = await new STLLoader().loadAsync(entry.url);
            geometry.computeBoundingBox();
            const mesh = new THREE.Mesh(geometry, makeSteelMaterial(ASSEMBLY_COLORS[index % ASSEMBLY_COLORS.length]));
            mesh.userData.partName = entry.name;
            attachEdgeLines(mesh);
            const position = entry.position;
            if (Array.isArray(position) && position.length === 3) {
              mesh.position.set(position[0], position[1], position[2]);
            }
            const rotation = entry.rotationDeg;
            if (Array.isArray(rotation) && rotation.length === 2
                && Array.isArray(rotation[1]) && rotation[1].length === 3) {
              const axis = new THREE.Vector3(rotation[1][0], rotation[1][1], rotation[1][2]);
              if (axis.lengthSq() > 1e-9) {
                mesh.quaternion.setFromAxisAngle(axis.normalize(), (rotation[0] * Math.PI) / 180);
              }
            }
            group.add(mesh);
          }
          modelRef.current = group;
          scene.add(group);
          fitCamera(group);
          setStatus("stl_loaded");
          return;
        } catch {
          setStatus("load_failed");
          return;
        }
      }
      if (objUrl) {
        setStatus("loading_obj");
        try {
          const object = await new OBJLoader().loadAsync(objUrl);
          modelRef.current = object;
          scene.add(object);
          fitCamera(object);
          applyWireframe(object, viewMode === "wireframe");
          setStatus("obj_loaded");
          return;
        } catch {
          setStatus("obj_fallback");
        }
      }
      if (stlUrl) {
        setStatus("loading_stl");
        try {
          const geometry = await new STLLoader().loadAsync(stlUrl);
          geometry.computeBoundingBox();
          geometry.center();
          const mesh = new THREE.Mesh(geometry, makeSteelMaterial(0x6f8a83));
          attachEdgeLines(mesh);
          modelRef.current = mesh;
          scene.add(mesh);
          fitCamera(mesh);
          setStatus("stl_loaded");
          return;
        } catch {
          setStatus("load_failed");
        }
      } else {
        setStatus("waiting");
      }
    };

    void loadModel();

    const onResize = () => {
      const nextWidth = Math.max(1, mount.clientWidth);
      const nextHeight = Math.max(1, mount.clientHeight);
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
    };

    window.addEventListener("resize", onResize);

    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      window.cancelAnimationFrame(frameId);
      controls.dispose();
      pmrem.dispose();
      envTexture.dispose();
      renderer.dispose();
      mount.innerHTML = "";
      cameraRef.current = null;
      controlsRef.current = null;
      modelRef.current = null;
      boundsRef.current = null;
    };
  }, [loaderKey, objUrl, stlUrl, modelsKey, viewMode]);

  // v0.14 F2a：装配显隐 + 点选高亮（不重载，只改材质/可见性；边线随父件自动隐藏）
  useEffect(() => {
    const root = modelRef.current;
    if (!root) {
      return;
    }
    root.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh || !mesh.userData.partName || mesh.userData.isEdgeHelper) {
        return;
      }
      mesh.visible = !(hidden || []).includes(String(mesh.userData.partName));
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      const isSelected = String(mesh.userData.partName) === (selected || "");
      materials.forEach((material) => {
        const physical = material as THREE.MeshPhysicalMaterial;
        if (physical && "emissive" in physical) {
          physical.emissive.setHex(isSelected ? 0x2fd8cf : 0x000000);
          physical.emissiveIntensity = isSelected ? 0.55 : 1;
        }
      });
    });
  }, [hidden, selected, status]);

  const setPreset = (preset: ViewPreset) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) {
      return;
    }

    const center = controls.target.clone();
    const bounds = boundsRef.current;
    const size = bounds ? bounds.getSize(new THREE.Vector3()) : new THREE.Vector3(120, 120, 120);
    const distance = Math.max(size.length(), 60) * 0.9;

    switch (preset) {
      case "front":
        camera.position.set(center.x, center.y - distance, center.z);
        break;
      case "top":
        camera.position.set(center.x, center.y, center.z + distance);
        break;
      case "right":
        camera.position.set(center.x + distance, center.y, center.z);
        break;
      case "iso":
        camera.position.set(center.x + distance, center.y + distance, center.z + distance);
        break;
      case "fit":
        if (bounds) {
          const fitSize = bounds.getSize(new THREE.Vector3());
          const fitCenter = bounds.getCenter(new THREE.Vector3());
          const fitDistance = Math.max(fitSize.length(), 60) * 0.95;
          camera.position.set(fitCenter.x + fitDistance, fitCenter.y + fitDistance, fitCenter.z + fitDistance);
          controls.target.copy(fitCenter);
        }
        break;
    }
    controls.update();
    camera.lookAt(controls.target);
  };

  const toggleWireframe = () => {
    const root = modelRef.current;
    if (root) {
      root.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (!mesh.isMesh) {
          return;
        }
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        materials.forEach((material) => {
          if (material && "wireframe" in material) {
            (material as THREE.MeshStandardMaterial).wireframe = viewMode !== "wireframe";
            material.needsUpdate = true;
          }
        });
      });
    }
    setViewMode((current) => (current === "wireframe" ? "shaded" : "wireframe"));
  };

  const statusText = t(`viewport.${status}`);
  const showOverlay = status !== "obj_loaded" && status !== "stl_loaded";

  return (
    <div className="viewport-shell">
      <div className="viewport-toolbar">
        <button type="button" onClick={() => setPreset("fit")}>
          {t("viewport.fit")}
        </button>
        <button type="button" onClick={() => setPreset("iso")}>
          {t("viewport.iso")}
        </button>
        <button type="button" onClick={() => setPreset("front")}>
          {t("viewport.front")}
        </button>
        <button type="button" onClick={() => setPreset("top")}>
          {t("viewport.top")}
        </button>
        <button type="button" onClick={() => setPreset("right")}>
          {t("viewport.right")}
        </button>
        <button type="button" className={viewMode === "wireframe" ? "active" : ""} onClick={toggleWireframe}>
          {t("viewport.wireframe")}
        </button>
      </div>

      <div className="viewport-breadcrumb">{breadcrumb || t("viewport.breadcrumb")}</div>
      <div className="view-cube" aria-label="ViewCube">
        <button type="button" title={t("viewport.iso.title")} onClick={() => setPreset("iso")}>{t("viewport.face.iso")}</button>
        <div className="cube-face-row">
          <button type="button" title={t("viewport.top.title")} onClick={() => setPreset("top")}>{t("viewport.face.top")}</button>
          <button type="button" title={t("viewport.front.title")} onClick={() => setPreset("front")}>{t("viewport.face.front")}</button>
          <button type="button" title={t("viewport.right.title")} onClick={() => setPreset("right")}>{t("viewport.face.right")}</button>
        </div>
        <button type="button" title={t("viewport.fit.title")} onClick={() => setPreset("fit")}>{t("viewport.fit")}</button>
      </div>

      <div className="viewport-canvas" ref={mountRef} />

      <div className="viewport-axes" aria-label={t("viewport.axes")}>
        <span className="axis-x">X</span>
        <span className="axis-y">Y</span>
        <span className="axis-z">Z</span>
      </div>
      <div className="viewport-status">{statusLabel || statusText}</div>

      {showOverlay && (
        <div className="viewport-empty">
          <strong>{statusText}</strong>
          <span>{t("viewport.empty.hint")}</span>
        </div>
      )}
    </div>
  );
}