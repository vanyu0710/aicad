import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

type Props = {
  objUrl?: string;
  stlUrl?: string;
  breadcrumb?: string;
  statusLabel?: string;
};

type ViewPreset = "iso" | "front" | "top" | "right" | "fit";

export default function Viewport({ objUrl, stlUrl, breadcrumb, statusLabel }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const modelRef = useRef<THREE.Object3D | null>(null);
  const boundsRef = useRef<THREE.Box3 | null>(null);
  const [status, setStatus] = useState("等待模型");
  const [viewMode, setViewMode] = useState<"shaded" | "wireframe">("shaded");
  const loaderKey = useMemo(() => `${objUrl || ""}:${stlUrl || ""}`, [objUrl, stlUrl]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) {
      return;
    }

    const width = Math.max(1, mount.clientWidth);
    const height = Math.max(1, mount.clientHeight);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf6f4ee);

    const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 4000);
    camera.position.set(110, 90, 110);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setClearColor(0xf6f4ee, 1);
    mount.innerHTML = "";
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controlsRef.current = controls;

    const ambient = new THREE.AmbientLight(0xffffff, 1.5);
    scene.add(ambient);

    const dir1 = new THREE.DirectionalLight(0xffffff, 1.15);
    dir1.position.set(90, 140, 90);
    scene.add(dir1);

    const dir2 = new THREE.DirectionalLight(0xf3efe5, 0.7);
    dir2.position.set(-80, 60, -60);
    scene.add(dir2);

    const grid = new THREE.GridHelper(220, 22, 0x7f8f88, 0xb7b1a7);
    grid.material.opacity = 0.2;
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
        modelRef.current = null;
      }
    };

    const loadModel = async () => {
      clearModel();
      if (objUrl) {
        setStatus("正在加载 OBJ");
        try {
          const object = await new OBJLoader().loadAsync(objUrl);
          modelRef.current = object;
          scene.add(object);
          fitCamera(object);
          applyWireframe(object, viewMode === "wireframe");
          setStatus("OBJ 已加载");
          return;
        } catch {
          setStatus("OBJ 加载失败，尝试 STL");
        }
      }
      if (stlUrl) {
        setStatus("正在加载 STL");
        try {
          const geometry = await new STLLoader().loadAsync(stlUrl);
          geometry.computeBoundingBox();
          geometry.center();
          const material = new THREE.MeshStandardMaterial({ color: 0x5f746c, metalness: 0.08, roughness: 0.72 });
          material.wireframe = viewMode === "wireframe";
          const mesh = new THREE.Mesh(geometry, material);
          modelRef.current = mesh;
          scene.add(mesh);
          fitCamera(mesh);
          setStatus("STL 已加载");
          return;
        } catch {
          setStatus("模型加载失败");
        }
      } else {
        setStatus("等待模型");
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
      renderer.dispose();
      mount.innerHTML = "";
      cameraRef.current = null;
      controlsRef.current = null;
      modelRef.current = null;
      boundsRef.current = null;
    };
  }, [loaderKey, objUrl, stlUrl, viewMode]);

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

  const showOverlay = status !== "OBJ 已加载" && status !== "STL 已加载";

  return (
    <div className="viewport-shell">
      <div className="viewport-toolbar">
        <button type="button" onClick={() => setPreset("fit")}>
          适配
        </button>
        <button type="button" onClick={() => setPreset("iso")}>
          等轴
        </button>
        <button type="button" onClick={() => setPreset("front")}>
          前视
        </button>
        <button type="button" onClick={() => setPreset("top")}>
          俯视
        </button>
        <button type="button" onClick={() => setPreset("right")}>
          右视
        </button>
        <button type="button" className={viewMode === "wireframe" ? "active" : ""} onClick={toggleWireframe}>
          线框
        </button>
      </div>

            <div className="viewport-breadcrumb">{breadcrumb || "草稿 / FeaturePlan"}</div>
      <div className="view-cube" aria-label="ViewCube">
        <button type="button" title="等轴测视图" onClick={() => setPreset("iso")}>等轴</button>
        <div className="cube-face-row">
          <button type="button" title="俯视图" onClick={() => setPreset("top")}>上</button>
          <button type="button" title="前视图" onClick={() => setPreset("front")}>前</button>
          <button type="button" title="右视图" onClick={() => setPreset("right")}>右</button>
        </div>
        <button type="button" title="适配模型" onClick={() => setPreset("fit")}>适配</button>
      </div>

      <div className="viewport-canvas" ref={mountRef} />

      <div className="viewport-axes" aria-label="坐标轴">
        <span className="axis-x">X</span>
        <span className="axis-y">Y</span>
        <span className="axis-z">Z</span>
      </div>
      <div className="viewport-status">{statusLabel || status}</div>

      {showOverlay && (
        <div className="viewport-empty">
          <strong>{status}</strong>
          <span>左侧输入草图与尺寸，生成后这里会显示 STL / OBJ 预览。</span>
        </div>
      )}
    </div>
  );
}
