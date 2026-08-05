import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

type Props = {
  objUrl?: string;
  stlUrl?: string;
};

export default function Viewport({ objUrl, stlUrl }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState("等待模型");
  const loaderKey = useMemo(() => `${objUrl || ""}:${stlUrl || ""}`, [objUrl, stlUrl]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) {
      return;
    }

    const width = mount.clientWidth;
    const height = mount.clientHeight;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf6f4ee);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
    camera.position.set(80, 80, 80);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    mount.innerHTML = "";
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    const ambient = new THREE.AmbientLight(0xffffff, 1.4);
    scene.add(ambient);
    const dir = new THREE.DirectionalLight(0xfaf4e8, 1.2);
    dir.position.set(80, 120, 100);
    scene.add(dir);

    const grid = new THREE.GridHelper(160, 16, 0x809089, 0xb7b1a2);
    grid.material.opacity = 0.18;
    grid.material.transparent = true;
    scene.add(grid);

    const axis = new THREE.AxesHelper(40);
    scene.add(axis);

    const box = new THREE.Mesh(
      new THREE.BoxGeometry(24, 16, 10),
      new THREE.MeshStandardMaterial({ color: 0x8ea29a, metalness: 0.08, roughness: 0.8 }),
    );
    scene.add(box);

    let disposed = false;
    const animate = () => {
      if (disposed) {
        return;
      }
      box.rotation.y += 0.008;
      controls.update();
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    };
    animate();

    const loadModel = async () => {
      if (objUrl) {
        setStatus("加载 OBJ");
        try {
          const geometry = await new OBJLoader().loadAsync(objUrl);
          scene.remove(box);
          scene.add(geometry);
          setStatus("OBJ 已加载");
          return;
        } catch {
          // fall through to STL
        }
      }
      if (stlUrl) {
        setStatus("加载 STL");
        try {
          const geometry = await new STLLoader().loadAsync(stlUrl);
          const material = new THREE.MeshStandardMaterial({ color: 0x5f746c, metalness: 0.08, roughness: 0.75 });
          const mesh = new THREE.Mesh(geometry, material);
          geometry.computeBoundingBox();
          geometry.center();
          scene.remove(box);
          scene.add(mesh);
          setStatus("STL 已加载");
        } catch {
          setStatus("模型加载失败，显示占位体");
        }
      }
    };
    loadModel();

    const onResize = () => {
      const nextWidth = mount.clientWidth;
      const nextHeight = mount.clientHeight;
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
    };
    window.addEventListener("resize", onResize);

    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      controls.dispose();
      renderer.dispose();
      mount.innerHTML = "";
    };
  }, [loaderKey, objUrl, stlUrl]);

  return (
    <div className="viewport-shell">
      <div className="viewport-canvas" ref={mountRef} />
      <div className="viewport-badge">{status}</div>
    </div>
  );
}
