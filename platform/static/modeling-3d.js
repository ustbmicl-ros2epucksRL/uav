import * as THREE from 'three';
import {OrbitControls} from './vendor/three/OrbitControls.js';
import {localFrame, imageryGround, groundBase} from './imagery-ground.js';

const prefix = location.pathname.startsWith('/network') ? '/network' : '';
const world = (x, y, z = 0) => new THREE.Vector3(x - 1000, z, 1000 - y);
const TYPE_HEX = {RADAR: 0x4bcffa, EO: 0xffbe55, RF: 0xc58af9, IR: 0xff7979, ADSB: 0x55efc4};

function clearGroup(group) {
  for (const child of [...group.children]) {
    group.remove(child);
    child.traverse(o => {
      o.geometry?.dispose?.();
      for (const m of [].concat(o.material || [])) m?.dispose?.();
    });
  }
}

const host = document.getElementById('modeling-3d-viewport');
if (host) {
  try {
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x142c40, 0.00012);
    const camera = new THREE.PerspectiveCamera(44, 16 / 9, 5, 38000);
    const renderer = new THREE.WebGLRenderer({antialias: true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.46;
    controls.minDistance = 260;
    controls.maxDistance = 4200;

    scene.add(new THREE.HemisphereLight(0xd9effc, 0x243849, 0.9));
    const sun = new THREE.DirectionalLight(0xfff6e8, 1.32);
    sun.position.set(-900, 1300, 700);
    scene.add(sun);
    scene.add(groundBase(50000, 0x152b3c));

    const frame = localFrame(39.0485, 115.9865);
    const imagery = imageryGround({
      renderer, frame, centre: [1000, 1000],
      url: (z, x, y) => `${prefix}/tiles/imagery/${z}/${x}/${y}`,
      place: (e, n) => world(e, n, 0),
      levels: [{z: 16, from: [-400, -400], to: [2400, 2400]}, {z: 14, from: [-4000, -4000], to: [6000, 6000]}],
      onStatus: () => {},
    });
    scene.add(imagery.group);
    imagery.load();

    const content = new THREE.Group();
    scene.add(content);

    const homeView = () => { controls.target.set(0, 55, 0); camera.position.set(-650, 1240, 1450); controls.update(); };
    const sideView = () => { controls.target.set(0, 65, 0); camera.position.set(-1400, 520, 820); controls.update(); };
    const topView = () => { controls.target.set(0, 0, 0); camera.position.set(0, 2300, 12); controls.update(); };
    homeView();

    document.getElementById('mod3d-reset')?.addEventListener('click', homeView);
    document.getElementById('mod3d-side')?.addEventListener('click', sideView);
    document.getElementById('mod3d-top')?.addEventListener('click', topView);

    new ResizeObserver(() => {
      const w = host.clientWidth || 900, h = host.clientHeight || 460;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    }).observe(host);

    renderer.setAnimationLoop(() => {
      if (!host.clientWidth || !host.clientHeight) return;
      controls.update();
      renderer.render(scene, camera);
    });

    window.Modeling3D = {
      update(spec, result) {
        clearGroup(content);
        if (!spec || !result) return;
        const sit = result.siting || {};
        const rt = result.routing || {};
        // Buildings
        for (const b of (sit.buildings || []).slice(0, 320)) {
          const h = Math.max(6, b.h || 20);
          const geom = new THREE.BoxGeometry(b.w, h, b.d);
          const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
            color: new THREE.Color().setHSL(0.57, 0.2, Math.max(0.38, 0.72 - h / 220)),
            roughness: 0.55, metalness: 0.12, transparent: true, opacity: 0.88,
          }));
          mesh.position.copy(world(b.x + b.w / 2, b.y + b.d / 2, h / 2));
          content.add(mesh);
        }
        // Core focus zone wireframe
        if (sit.zone) {
          const z = sit.zone, corners = [[z.x0, z.y0], [z.x1, z.y0], [z.x1, z.y1], [z.x0, z.y1]];
          content.add(new THREE.LineLoop(
            new THREE.BufferGeometry().setFromPoints(corners.map(([x, y]) => world(x, y, 160))),
            new THREE.LineBasicMaterial({color: 0xf5b942, transparent: true, opacity: 0.8})
          ));
        }
        // NFZs
        for (const z of spec.routing?.no_fly_zones || []) {
          const w = z.x1 - z.x0, d = z.y1 - z.y0, h = (z.z1 || 200) - (z.z0 || 0);
          const box = new THREE.Mesh(
            new THREE.BoxGeometry(w, h, d),
            new THREE.MeshBasicMaterial({color: 0xe74c3c, transparent: true, opacity: 0.16, depthWrite: false})
          );
          box.position.copy(world(z.x0 + w / 2, z.y0 + d / 2, (z.z0 || 0) + h / 2));
          content.add(box);
        }
        // Authorized Countermeasure Sites & EMC Isolation Rings
        const isoR = spec.siting?.min_jammer_rf_isolation_m || 180;
        const cmSites = sit.countermeasure_assessment?.sites || spec.siting?.countermeasure_sites || [
          {id: 'CM-AUTH-01', x: 680, y: 680, z: 48, azimuth: 45, fov: 85, range_m: 560},
        ];
        for (const cm of cmSites) {
          const mast = new THREE.Mesh(
            new THREE.CylinderGeometry(1.4, 2.8, cm.z || 48, 8),
            new THREE.MeshStandardMaterial({color: 0xe67e22, emissive: 0xc0392b, emissiveIntensity: 0.35})
          );
          mast.position.copy(world(cm.x, cm.y, (cm.z || 48) / 2));
          content.add(mast);
          const ringPts = [];
          for (let i = 0; i <= 64; i++) {
            const a = (i / 64) * Math.PI * 2;
            ringPts.push(world(cm.x + Math.sin(a) * isoR, cm.y + Math.cos(a) * isoR, 3));
          }
          const ring = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(ringPts),
            new THREE.LineDashedMaterial({color: 0x2ecc71, dashSize: 16, gapSize: 10})
          );
          ring.computeLineDistances();
          content.add(ring);

          const fov = cm.fov || 85, reach = cm.range_m || 560, orig = world(cm.x, cm.y, cm.z || 48), verts = [];
          for (let k = 0; k < 14; k++) {
            const a1 = ((cm.azimuth || 45) - fov / 2 + (fov * k) / 14) * Math.PI / 180;
            const a2 = ((cm.azimuth || 45) - fov / 2 + (fov * (k + 1)) / 14) * Math.PI / 180;
            const p1 = world(cm.x + Math.sin(a1) * reach, cm.y + Math.cos(a1) * reach, 150);
            const p2 = world(cm.x + Math.sin(a2) * reach, cm.y + Math.cos(a2) * reach, 150);
            verts.push(orig.x, orig.y, orig.z, p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
          }
          const fg = new THREE.BufferGeometry();
          fg.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
          content.add(new THREE.Mesh(fg, new THREE.MeshBasicMaterial({
            color: 0xf39c12, transparent: true, opacity: 0.2, side: THREE.DoubleSide, depthWrite: false,
          })));
        }
        // Deployed Sensing Towers & FOV Cones
        const addedSet = new Set((sit.selected_candidates || []).map(c => c.id));
        for (const d of sit.deployed_network || []) {
          const hex = TYPE_HEX[d.type] || 0x4bcffa;
          const isNew = addedSet.has(d.id);
          const tower = new THREE.Mesh(
            new THREE.CylinderGeometry(1.1, 2.2, d.z || 110, 8),
            new THREE.MeshStandardMaterial({color: isNew ? 0xffffff : hex, roughness: 0.4})
          );
          tower.position.copy(world(d.x, d.y, (d.z || 110) / 2));
          content.add(tower);
          const fov = Math.min(d.fov || 180, 360), reach = Math.min(d.range || 600, 420), orig = world(d.x, d.y, d.z || 110), verts = [];
          const steps = Math.max(10, Math.round(fov / 15));
          for (let k = 0; k < steps; k++) {
            const a1 = ((d.azimuth || 0) - fov / 2 + (fov * k) / steps) * Math.PI / 180;
            const a2 = ((d.azimuth || 0) - fov / 2 + (fov * (k + 1)) / steps) * Math.PI / 180;
            const p1 = world(d.x + Math.sin(a1) * reach, d.y + Math.cos(a1) * reach, 55);
            const p2 = world(d.x + Math.sin(a2) * reach, d.y + Math.cos(a2) * reach, 55);
            verts.push(orig.x, orig.y, orig.z, p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
          }
          const fg = new THREE.BufferGeometry();
          fg.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
          content.add(new THREE.Mesh(fg, new THREE.MeshBasicMaterial({
            color: hex, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false,
          })));
        }
        // 3D Sensing-Coupled Route Tube
        const samples = rt.samples || [];
        for (let i = 1; i < samples.length; i++) {
          const p0 = world(samples[i - 1].x, samples[i - 1].y, samples[i - 1].z || 125);
          const p1 = world(samples[i].x, samples[i].y, samples[i].z || 125);
          if (p0.distanceTo(p1) < 0.2) continue;
          const ok = samples[i].covered !== false;
          content.add(new THREE.Mesh(
            new THREE.TubeGeometry(new THREE.LineCurve3(p0, p1), 1, 3.6, 6, false),
            new THREE.MeshStandardMaterial({color: ok ? 0x2ecc71 : 0xe74c3c, emissive: ok ? 0x27ae60 : 0xc0392b, emissiveIntensity: 0.4})
          ));
        }
      },
    };
    window.dispatchEvent(new Event('modeling-3d-ready'));
  } catch {
    host.textContent = '当前环境未启用 WebGL，请查看下方平面耦合视图。';
  }
}
