// Unified Low-Altitude 3D Digital Twin WebGL Engine (Three.js + WGS84 Satellite Imagery + Real Polygon/High-Rise Buildings + 3D Radar/EO/RF/UAV + Volumetric FOV + Live LoS Beams + 3D CRLB Error Ellipsoid).
import * as THREE from 'three';
import {OrbitControls} from './vendor/three/OrbitControls.js';
import {localFrame, imageryGround, groundBase} from './imagery-ground.js';

const prefix = location.pathname.startsWith('/network') ? '/network' : '';
const STATE_HEX = {ok: 0x2ecc71, imprecise: 0xf1c40f, unlocatable: 0xe67e22, blind: 0xe74c3c, invalid: 0x95a5a6};
const TYPE_HEX = {RADAR: 0x4bcffa, EO: 0xffbe55, RF: 0xc58af9, IR: 0xff7979, ADSB: 0x55efc4};

const world = (x, y, z = 0) => new THREE.Vector3(x - 1000, z, 1000 - y);

function createFacadeTexture() {
    const c = document.createElement('canvas');
    c.width = 128;
    c.height = 256;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#4c657a';
    ctx.fillRect(0, 0, 128, 256);
    for (let y = 6; y < 256; y += 14) {
        ctx.fillStyle = 'rgba(18, 34, 48, 0.52)';
        ctx.fillRect(0, y, 128, 7);
        for (let x = 6; x < 124; x += 16) {
            ctx.fillStyle = ((x + y) % 48 === 0) ? 'rgba(215, 238, 252, 0.58)' : 'rgba(130, 175, 206, 0.36)';
            ctx.fillRect(x, y + 1, 10, 5);
        }
    }
    const tex = new THREE.CanvasTexture(c);
    tex.wrapS = THREE.RepeatWrapping;
    tex.wrapT = THREE.RepeatWrapping;
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
}

function createSkyDome() {
    const c = document.createElement('canvas');
    c.width = 16;
    c.height = 256;
    const ctx = c.getContext('2d');
    const grad = ctx.createLinearGradient(0, 0, 0, 256);
    grad.addColorStop(0.0, '#0d2136');
    grad.addColorStop(0.42, '#1d4262');
    grad.addColorStop(0.68, '#467496');
    grad.addColorStop(1.0, '#1a2f40');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 16, 256);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(26000, 24, 16),
        new THREE.MeshBasicMaterial({map: tex, side: THREE.BackSide, depthWrite: false})
    );
    mesh.raycast = () => {};
    return mesh;
}

function makeLabelSprite(text, borderHex = '#58c7e8') {
    const c = document.createElement('canvas');
    const ctx = c.getContext('2d');
    ctx.font = '600 26px -apple-system, "PingFang SC", sans-serif';
    const w = Math.min(520, Math.ceil(ctx.measureText(text).width) + 32);
    c.width = w;
    c.height = 50;
    ctx.font = '600 26px -apple-system, "PingFang SC", sans-serif';
    ctx.fillStyle = 'rgba(11, 25, 38, 0.86)';
    ctx.strokeStyle = borderHex;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(2, 2, w - 4, 46, 10);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#eef7fc';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 15, 26);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, transparent: true, depthTest: false, sizeAttenuation: false}));
    sprite.scale.set((w / 50) * 0.024, 0.024, 1);
    sprite.center.set(0.5, -0.2);
    sprite.raycast = () => {};
    return sprite;
}

function rayOccluded(sx, sy, sz, tx, ty, tz, buildings) {
    const dx = tx - sx, dy = ty - sy, dz = tz - sz;
    for (const b of buildings) {
        let tmin = 0, tmax = 1, hit = true;
        const slabs = [[sx, dx, b.x, b.x + b.w], [sy, dy, b.y, b.y + b.d], [sz, dz, 0, b.h]];
        for (const [orig, dir, lo, hi] of slabs) {
            if (Math.abs(dir) < 1e-8) {
                if (orig < lo || orig > hi) { hit = false; break; }
            } else {
                let t1 = (lo - orig) / dir, t2 = (hi - orig) / dir;
                if (t1 > t2) [t1, t2] = [t2, t1];
                tmin = Math.max(tmin, t1);
                tmax = Math.min(tmax, t2);
                if (tmin > tmax) { hit = false; break; }
            }
        }
        if (hit && tmin < 0.995 && tmax > 0.005) {
            return [sx + dx * tmin, sy + dy * tmin, sz + dz * tmin];
        }
    }
    return null;
}

function inSensorFov(d, tx, ty, tz) {
    const dx = tx - d.x, dy = ty - d.y, dz = tz - d.z;
    const dist = Math.hypot(dx, dy, dz);
    if (dist > (d.range || 1000) || dist < 1e-3) return false;
    const fov = d.fov ?? 360;
    if (fov < 359.5) {
        const bearing = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
        const diff = Math.abs(((bearing - (d.azimuth || 0) + 540) % 360) - 180);
        if (diff > fov / 2) return false;
    }
    return true;
}

function clearGroup(group) {
    for (const child of [...group.children]) {
        group.remove(child);
        child.traverse(obj => {
            obj.geometry?.dispose?.();
            const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
            for (const m of mats) {
                if (!m) continue;
                if (m.map && !m.userData?.sharedTexture) m.map.dispose?.();
                m.dispose?.();
            }
        });
    }
}

function buildDroneMesh(accentHex = 0x00d8d6, scaleVal = 2.1) {
    const root = new THREE.Group();
    const bodyMat = new THREE.MeshStandardMaterial({color: 0xf4f8fb, roughness: 0.32, metalness: 0.35});
    const armMat = new THREE.MeshStandardMaterial({color: 0x1e2c3a, roughness: 0.5, metalness: 0.45});
    const accentMat = new THREE.MeshStandardMaterial({color: accentHex, emissive: accentHex, emissiveIntensity: 0.42});
    const fuselage = new THREE.Mesh(new THREE.BoxGeometry(5.5, 1.8, 7.2), bodyMat);
    root.add(fuselage);
    const canopy = new THREE.Mesh(new THREE.SphereGeometry(2.3, 14, 10), accentMat);
    canopy.scale.set(1, 0.55, 1.35);
    canopy.position.set(0, 0.9, -0.6);
    root.add(canopy);
    const rotors = [];
    for (const [sx, sz, navHex] of [[-1, -1, 0x2ecc71], [1, -1, 0x2ecc71], [-1, 1, 0xff5252], [1, 1, 0xff5252]]) {
        const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 8.2, 8), armMat);
        arm.rotation.z = Math.PI / 2;
        arm.rotation.y = Math.atan2(sz, sx);
        arm.position.set(sx * 3.2, 0.2, sz * 3.2);
        root.add(arm);
        const motor = new THREE.Mesh(new THREE.CylinderGeometry(0.85, 0.85, 1.2, 10), armMat);
        motor.position.set(sx * 5.8, 0.6, sz * 5.8);
        root.add(motor);
        const disc = new THREE.Mesh(
            new THREE.CylinderGeometry(3.2, 3.2, 0.14, 20),
            new THREE.MeshBasicMaterial({color: 0x9be7ff, transparent: true, opacity: 0.48, depthWrite: false})
        );
        disc.position.set(sx * 5.8, 1.25, sz * 5.8);
        rotors.push(disc);
        root.add(disc);
        const led = new THREE.Mesh(new THREE.SphereGeometry(0.48, 8, 8), new THREE.MeshBasicMaterial({color: navHex}));
        led.position.set(sx * 5.8, -0.1, sz * 5.8);
        root.add(led);
    }
    const nose = new THREE.Mesh(new THREE.ConeGeometry(1.1, 3.0, 12), accentMat);
    nose.rotation.x = -Math.PI / 2;
    nose.position.set(0, 0.1, -5.0);
    root.add(nose);
    root.scale.setScalar(scaleVal);
    root.userData.rotors = rotors;
    return root;
}

class TwinEngine {
    constructor(host) {
        this.host = host;
        this.scene = new THREE.Scene();
        this.scene.fog = new THREE.FogExp2(0x162d42, 0.00011);
        this.camera = new THREE.PerspectiveCamera(44, 16 / 9, 4, 42000);
        this.renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'high-performance'});
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        host.appendChild(this.renderer.domElement);

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.07;
        this.controls.minPolarAngle = 0.10;
        this.controls.maxPolarAngle = Math.PI * 0.465;
        this.controls.minDistance = 220;
        this.controls.maxDistance = 4200;

        this.scene.add(createSkyDome());
        this.scene.add(new THREE.HemisphereLight(0xd9effc, 0x243849, 0.88));
        const sun = new THREE.DirectionalLight(0xfff6e8, 1.38);
        sun.position.set(-950, 1350, 720);
        this.scene.add(sun);
        const rim = new THREE.DirectionalLight(0x7ec8f5, 0.42);
        rim.position.set(1100, 620, -880);
        this.scene.add(rim);

        this.baseGround = groundBase(60000, 0x162c3d);
        this.scene.add(this.baseGround);

        this.gridGroup = new THREE.Group();
        this.buildingsGroup = new THREE.Group();
        this.devicesGroup = new THREE.Group();
        this.fovGroup = new THREE.Group();
        this.voxelsGroup = new THREE.Group();
        this.counterGroup = new THREE.Group();
        this.trackGroup = new THREE.Group();
        this.losGroup = new THREE.Group();
        this.guidanceGroup = new THREE.Group();
        this.actorGroup = new THREE.Group();
        this.scene.add(this.gridGroup, this.buildingsGroup, this.devicesGroup, this.fovGroup, this.voxelsGroup, this.counterGroup, this.trackGroup, this.losGroup, this.guidanceGroup, this.actorGroup);

        this.facadeTex = createFacadeTexture();
        this.drone = buildDroneMesh(0xff6b6b, 2.15);
        this.coopDrone = buildDroneMesh(0x2ecc71, 1.65);
        this.coopBadge = makeLabelSprite('合作物流机(白名单)', '#2ecc71');
        this.ellipsoidMesh = new THREE.Mesh(
            new THREE.SphereGeometry(1, 20, 14),
            new THREE.MeshBasicMaterial({color: 0x2ecc71, transparent: true, opacity: 0.20, depthWrite: false})
        );
        this.ellipsoidWire = new THREE.LineSegments(
            new THREE.EdgesGeometry(new THREE.SphereGeometry(1, 14, 10)),
            new THREE.LineBasicMaterial({color: 0x69f0ae, transparent: true, opacity: 0.68})
        );
        this.dropLine = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
            new THREE.LineDashedMaterial({color: 0xffffff, dashSize: 8, gapSize: 6, transparent: true, opacity: 0.65})
        );
        this.actorGroup.add(this.drone, this.coopDrone, this.coopBadge, this.ellipsoidMesh, this.ellipsoidWire, this.dropLine);
        this.actorGroup.visible = false;
        this.counterSignature = '';

        this.anchorKey = '';
        this.imagery = null;
        this.tileSummary = '正在连接卫星底图…';
        this.onTileStatus = null;
        this.onPickSample = null;
        this.onInspect = null;
        this.samplePickSpheres = [];
        this.currentState = null;
        this.sceneSignature = '';
        this.deviceSignature = '';
        this.trackSignature = '';

        this._buildSurveyGrid();
        this._bindInteraction();
        this.homeView();

        new ResizeObserver(() => this.resize()).observe(host);
        this.resize();
        this.renderer.setAnimationLoop(() => {
            if (!host.clientWidth || !host.clientHeight) return;
            this.controls.update();
            if (this.actorGroup.visible) {
                for (const r of this.drone.userData.rotors || []) r.rotation.y += 0.45;
                if (this.coopDrone.visible) {
                    for (const r of this.coopDrone.userData.rotors || []) r.rotation.y += 0.42;
                }
            }
            this.renderer.render(this.scene, this.camera);
        });
    }

    resize() {
        const w = this.host.clientWidth || 960;
        const h = this.host.clientHeight || 480;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h, false);
    }

    homeView() {
        this.controls.target.set(0, 55, 0);
        this.camera.position.set(-680, 1280, 1480);
        this.controls.update();
    }

    sideView() {
        this.controls.target.set(0, 65, 0);
        this.camera.position.set(-1420, 560, 860);
        this.controls.update();
    }

    topView() {
        this.controls.target.set(0, 0, 0);
        this.camera.position.set(0, 2350, 12);
        this.controls.update();
    }

    _buildSurveyGrid() {
        const pts = [];
        for (let v = 0; v <= 2000; v += 250) {
            pts.push(world(v, 0, 0.8), world(v, 2000, 0.8));
            pts.push(world(0, v, 0.8), world(2000, v, 0.8));
        }
        const grid = new THREE.LineSegments(
            new THREE.BufferGeometry().setFromPoints(pts),
            new THREE.LineBasicMaterial({color: 0x5ea2c7, transparent: true, opacity: 0.24})
        );
        grid.raycast = () => {};
        this.gridGroup.add(grid);
        const border = new THREE.LineLoop(
            new THREE.BufferGeometry().setFromPoints([world(0, 0, 1.2), world(2000, 0, 1.2), world(2000, 2000, 1.2), world(0, 2000, 1.2)]),
            new THREE.LineBasicMaterial({color: 0x74c0e3, transparent: true, opacity: 0.55})
        );
        border.raycast = () => {};
        this.gridGroup.add(border);
    }

    _bindInteraction() {
        const raycaster = new THREE.Raycaster();
        let down = null;
        const dom = this.renderer.domElement;
        dom.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
        dom.addEventListener('pointerup', e => {
            if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
            const rect = dom.getBoundingClientRect();
            const ndc = new THREE.Vector2(
                ((e.clientX - rect.left) / rect.width) * 2 - 1,
                -((e.clientY - rect.top) / rect.height) * 2 + 1
            );
            raycaster.setFromCamera(ndc, this.camera);
            if (this.samplePickSpheres.length) {
                const sampleHit = raycaster.intersectObjects(this.samplePickSpheres, false)[0];
                if (sampleHit && Number.isInteger(sampleHit.object.userData?.sampleIndex)) {
                    this.onPickSample?.(sampleHit.object.userData.sampleIndex);
                    return;
                }
            }
            const inspectTargets = [...this.devicesGroup.children, ...this.buildingsGroup.children];
            const hit = raycaster.intersectObjects(inspectTargets, true).find(h => h.object.userData?.inspectText);
            if (hit) this.onInspect?.(hit.object.userData.inspectText);
        });
    }

    _ensureSatelliteImagery(anchor, showImagery) {
        if (!anchor || !Number.isFinite(anchor.latitude) || !Number.isFinite(anchor.longitude)) return;
        const key = `${anchor.latitude.toFixed(5)},${anchor.longitude.toFixed(5)}`;
        if (key !== this.anchorKey) {
            this.anchorKey = key;
            this.imagery?.dispose?.();
            const frame = localFrame(anchor.latitude, anchor.longitude);
            this.imagery = imageryGround({
                renderer: this.renderer,
                frame,
                centre: [1000, 1000],
                url: (z, x, y) => `${prefix}/tiles/imagery/${z}/${x}/${y}`,
                place: (e, n) => world(e, n, 0),
                levels: [
                    {z: 16, from: [-400, -400], to: [2400, 2400]},
                    {z: 15, from: [-3200, -3200], to: [5200, 5200]},
                    {z: 13, from: [-10000, -10000], to: [12000, 12000]},
                ],
                onStatus: ({loaded, total, done, failed}) => {
                    this.tileSummary = !done
                        ? `卫星影像加载 ${loaded}/${total}`
                        : failed === total
                            ? '离线测绘底板（未连接外网瓦片）'
                            : failed > 0
                                ? `卫星影像 ${loaded}/${total} 已就绪`
                                : '真实卫星正射影像已就绪';
                    this.onTileStatus?.(this.tileSummary);
                },
            });
            this.scene.add(this.imagery.group);
            this.imagery.load();
        }
        if (this.imagery?.group) this.imagery.group.visible = showImagery !== false;
    }

    _rebuildBuildings(buildings, footprints, zone) {
        clearGroup(this.buildingsGroup);
        if (zone) {
            const z0 = zone.z0 ?? 0, z1 = zone.z1 ?? 160;
            const corners = [[zone.x0, zone.y0], [zone.x1, zone.y0], [zone.x1, zone.y1], [zone.x0, zone.y1]];
            for (const h of [z0 + 1.5, z1]) {
                const ring = new THREE.LineLoop(
                    new THREE.BufferGeometry().setFromPoints(corners.map(([x, y]) => world(x, y, h))),
                    new THREE.LineBasicMaterial({color: 0xf5b942, transparent: true, opacity: h === z1 ? 0.52 : 0.85})
                );
                ring.raycast = () => {};
                this.buildingsGroup.add(ring);
            }
            for (const [x, y] of corners) {
                const post = new THREE.Line(
                    new THREE.BufferGeometry().setFromPoints([world(x, y, z0 + 1.5), world(x, y, z1)]),
                    new THREE.LineBasicMaterial({color: 0xf5b942, transparent: true, opacity: 0.45})
                );
                post.raycast = () => {};
                this.buildingsGroup.add(post);
            }
        }

        if (Array.isArray(footprints) && footprints.length) {
            for (const fp of footprints) {
                const outer = fp.rings?.[0];
                if (!outer || outer.length < 4) continue;
                const shape = new THREE.Shape(outer.map(([x, y]) => new THREE.Vector2(x, y)));
                for (const hole of fp.rings.slice(1)) {
                    if (hole.length >= 4) shape.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y))));
                }
                const h = Math.max(3, fp.h || 9);
                const geom = new THREE.ExtrudeGeometry(shape, {depth: h, bevelEnabled: false, steps: 1});
                const pos = geom.getAttribute('position');
                for (let i = 0; i < pos.count; i++) {
                    const p = world(pos.getX(i), pos.getY(i), pos.getZ(i));
                    pos.setXYZ(i, p.x, p.y, p.z);
                }
                geom.computeVertexNormals();
                const ratio = Math.min(1, h / 140);
                const roofMat = new THREE.MeshStandardMaterial({
                    color: new THREE.Color().setHSL(0.57 - ratio * 0.05, 0.18, 0.74 - ratio * 0.18),
                    roughness: 0.65, metalness: 0.08,
                });
                const wallMat = new THREE.MeshStandardMaterial({
                    color: new THREE.Color().setHSL(0.58 - ratio * 0.06, 0.22, 0.60 - ratio * 0.18),
                    roughness: 0.55, metalness: 0.12, transparent: true, opacity: 0.90,
                });
                const mesh = new THREE.Mesh(geom, [roofMat, wallMat]);
                mesh.userData.inspectText = `${fp.name} · 真实轮廓拉伸 · 高度 ${h} m（${fp.estimated ? '模型估计高度' : '标注高度'}）`;
                this.buildingsGroup.add(mesh);
                const edges = new THREE.LineSegments(
                    new THREE.EdgesGeometry(geom, 24),
                    new THREE.LineBasicMaterial({color: 0xd0e4f2, transparent: true, opacity: 0.28})
                );
                edges.raycast = () => {};
                this.buildingsGroup.add(edges);
            }
            return;
        }

        for (let i = 0; i < (buildings || []).length; i++) {
            const b = buildings[i];
            const h = Math.max(6, b.h);
            const ratio = Math.min(1, (h - 20) / 90);
            const tex = this.facadeTex;
            const wallMat = new THREE.MeshStandardMaterial({
                map: tex,
                color: new THREE.Color().setHSL(0.57 - ratio * 0.04, 0.22, 0.84 - ratio * 0.24),
                roughness: 0.48, metalness: 0.18,
            });
            wallMat.userData.sharedTexture = true;
            const roofMat = new THREE.MeshStandardMaterial({
                color: new THREE.Color().setHSL(0.56, 0.14, 0.68 - ratio * 0.20),
                roughness: 0.68, metalness: 0.08,
            });
            const geom = new THREE.BoxGeometry(b.w, h, b.d);
            const mesh = new THREE.Mesh(geom, [wallMat, wallMat, roofMat, wallMat, wallMat, wallMat]);
            mesh.position.copy(world(b.x + b.w / 2, b.y + b.d / 2, h / 2));
            mesh.userData.inspectText = `建筑 #${i + 1} · 平面 ${b.w}×${b.d} m · 高度 ${b.h} m（参与三维射线遮挡计算）`;
            this.buildingsGroup.add(mesh);
            const parapet = new THREE.LineSegments(
                new THREE.EdgesGeometry(geom),
                new THREE.LineBasicMaterial({color: 0x9ec5de, transparent: true, opacity: 0.36})
            );
            parapet.position.copy(mesh.position);
            parapet.raycast = () => {};
            this.buildingsGroup.add(parapet);
            if (h > 45 && b.w > 55 && b.d > 55) {
                const cap = new THREE.Mesh(new THREE.BoxGeometry(b.w * 0.36, 4.2, b.d * 0.36), roofMat);
                cap.position.copy(world(b.x + b.w / 2, b.y + b.d / 2, h + 2.1));
                cap.raycast = () => {};
                this.buildingsGroup.add(cap);
            }
        }
    }

    _rebuildDevices(devices, addedIds, showFov, showLabels) {
        clearGroup(this.devicesGroup);
        clearGroup(this.fovGroup);
        const addedSet = new Set(addedIds || []);
        const darkMetal = new THREE.MeshStandardMaterial({color: 0x2a3642, roughness: 0.42, metalness: 0.65});
        const lightShell = new THREE.MeshStandardMaterial({color: 0xebf2f7, roughness: 0.45, metalness: 0.12});

        for (const d of devices || []) {
            const online = d.status !== 'offline';
            const isAdded = addedSet.has(d.id);
            const hex = !online ? 0x7f8c8d : (TYPE_HEX[d.type] || 0x5dade2);
            const paint = new THREE.MeshStandardMaterial({color: hex, roughness: 0.34, metalness: 0.25});
            const root = new THREE.Group();
            root.position.copy(world(d.x, d.y, 0));
            const inspectText = `${isAdded ? '[规划新增] ' : '[既有站址] '}${d.name || d.id} · ${d.type} · 挂高 ${d.z} m · 量程 ${d.range} m · 视场 ${d.fov}°`;

            const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 2.2, d.z, 8), darkMetal.clone());
            mast.position.y = d.z / 2;
            mast.userData.inspectText = inspectText;
            root.add(mast);

            const ring = new THREE.Mesh(
                new THREE.RingGeometry(isAdded ? 9 : 6, isAdded ? 14 : 9, 28),
                new THREE.MeshBasicMaterial({color: isAdded ? 0xffffff : hex, transparent: true, opacity: isAdded ? 0.88 : 0.55, side: THREE.DoubleSide, depthWrite: false})
            );
            ring.rotation.x = -Math.PI / 2;
            ring.position.y = 1.4;
            ring.raycast = () => {};
            root.add(ring);

            const head = new THREE.Group();
            head.position.y = d.z;
            head.rotation.y = -((d.azimuth || 0) * Math.PI) / 180;
            if (d.type === 'RADAR') {
                const dish = new THREE.Mesh(new THREE.SphereGeometry(8.5, 16, 12, 0, Math.PI * 2, 0, Math.PI / 2.2), paint);
                dish.rotation.x = Math.PI / 2.3;
                dish.position.set(0, 5, -1);
                dish.userData.inspectText = inspectText;
                head.add(dish);
            } else if (d.type === 'EO' || d.type === 'IR') {
                const pod = new THREE.Mesh(new THREE.BoxGeometry(6.5, 4.2, 5.2), lightShell.clone());
                pod.position.y = 3.5;
                pod.userData.inspectText = inspectText;
                const lens = new THREE.Mesh(new THREE.CylinderGeometry(1.4, 1.8, 4.5, 12), paint);
                lens.rotation.x = Math.PI / 2;
                lens.position.set(0, 3.5, -4.2);
                head.add(pod, lens);
            } else {
                const box = new THREE.Mesh(new THREE.BoxGeometry(5.0, 3.2, 4.0), lightShell.clone());
                box.position.y = 2.6;
                head.add(box);
                for (const sx of [-2.2, 2.2]) {
                    const whip = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 12, 6), paint);
                    whip.position.set(sx, 8.2, 0);
                    whip.userData.inspectText = inspectText;
                    head.add(whip);
                }
            }
            root.add(head);

            if (showLabels !== false) {
                const badge = makeLabelSprite(`${isAdded ? '★ ' : ''}${d.name || d.id}`, isAdded ? '#ffe082' : '#' + new THREE.Color(hex).getHexString());
                badge.position.set(0, d.z + 18, 0);
                root.add(badge);
            }
            this.devicesGroup.add(root);

            if (showFov !== false && online) {
                const reach = Math.min(d.range || 400, 420);
                const fov = Math.min(d.fov || 360, 360);
                const steps = Math.max(12, Math.round(fov / 12));
                const startDeg = (d.azimuth || 0) - fov / 2;
                const vertices = [];
                const origin = world(d.x, d.y, d.z);
                for (let k = 0; k < steps; k++) {
                    const a1 = ((startDeg + (fov * k) / steps) * Math.PI) / 180;
                    const a2 = ((startDeg + (fov * (k + 1)) / steps) * Math.PI) / 180;
                    const p1 = world(d.x + Math.sin(a1) * reach, d.y + Math.cos(a1) * reach, Math.max(28, d.z * 0.55));
                    const p2 = world(d.x + Math.sin(a2) * reach, d.y + Math.cos(a2) * reach, Math.max(28, d.z * 0.55));
                    vertices.push(origin.x, origin.y, origin.z, p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
                }
                const fanGeom = new THREE.BufferGeometry();
                fanGeom.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
                fanGeom.computeVertexNormals();
                const fan = new THREE.Mesh(
                    fanGeom,
                    new THREE.MeshBasicMaterial({color: hex, transparent: true, opacity: 0.13, side: THREE.DoubleSide, depthWrite: false})
                );
                fan.raycast = () => {};
                this.fovGroup.add(fan);
            }
        }
    }

    _rebuildTrack(points, timeline, segmentRange) {
        clearGroup(this.trackGroup);
        this.samplePickSpheres = [];
        if (!Array.isArray(points) || points.length < 2) return;

        const pickGeom = new THREE.SphereGeometry(12, 8, 6);
        const pickMat = new THREE.MeshBasicMaterial({visible: false});
        points.forEach(([x, y, z], i) => {
            const sp = new THREE.Mesh(pickGeom, pickMat);
            sp.position.copy(world(x, y, z));
            sp.userData.sampleIndex = i;
            this.trackGroup.add(sp);
            this.samplePickSpheres.push(sp);
            if (i % 6 === 0) {
                const curtain = new THREE.Line(
                    new THREE.BufferGeometry().setFromPoints([world(x, y, 0.5), world(x, y, z)]),
                    new THREE.LineBasicMaterial({color: 0x90caf9, transparent: true, opacity: 0.22})
                );
                curtain.raycast = () => {};
                this.trackGroup.add(curtain);
            }
        });

        for (let i = 1; i < points.length; i++) {
            const inRange = !segmentRange || (i > segmentRange.start && i <= segmentRange.end);
            const state = timeline?.[i]?.state || 'ok';
            const hex = inRange ? (STATE_HEX[state] || 0x5dade2) : 0x546e7a;
            const p0 = world(...points[i - 1]), p1 = world(...points[i]);
            if (p0.distanceTo(p1) < 0.05) continue;
            const segTube = new THREE.Mesh(
                new THREE.TubeGeometry(new THREE.LineCurve3(p0, p1), 1, inRange ? 3.2 : 1.4, 6, false),
                new THREE.MeshStandardMaterial({
                    color: hex, emissive: hex, emissiveIntensity: inRange ? 0.35 : 0.08,
                    roughness: 0.4, transparent: !inRange, opacity: inRange ? 1.0 : 0.45,
                })
            );
            segTube.raycast = () => {};
            this.trackGroup.add(segTube);
        }
    }

    setVoxels(voxels, mode, cellM = 80, pdThreshold = 0.5, precisionLimit = 30) {
        clearGroup(this.voxelsGroup);
        if (!Array.isArray(voxels) || !voxels.length) return;
        const size = Math.max(18, cellM * 0.68);
        const geom = new THREE.BoxGeometry(size, 10, size);
        const mat = new THREE.MeshBasicMaterial({transparent: true, opacity: 0.36, depthWrite: false});
        const inst = new THREE.InstancedMesh(geom, mat, voxels.length);
        const mtx = new THREE.Matrix4();
        const color = new THREE.Color();
        voxels.forEach((v, idx) => {
            const [x, y, z, pd, , crlb, count] = v;
            mtx.setPosition(world(x, y, z));
            inst.setMatrixAt(idx, mtx);
            if (mode === 'precision') {
                if (crlb == null) color.setHex(0xe74c3c);
                else if (crlb <= precisionLimit) color.setHex(0x2ecc71);
                else color.setHex(0xf39c12);
            } else {
                if (pd < pdThreshold) color.setHex(0xe74c3c);
                else if (count >= 2) color.setHex(0x3498db);
                else color.setHex(0x2ecc71);
            }
            inst.setColorAt(idx, color);
        });
        inst.instanceMatrix.needsUpdate = true;
        if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
        inst.raycast = () => {};
        this.voxelsGroup.add(inst);
    }

    clearVoxels() {
        clearGroup(this.voxelsGroup);
    }

    _rebuildCountermeasures(cmSites, showCountermeasure, jammingActive, showLabels) {
        clearGroup(this.counterGroup);
        if (showCountermeasure === false || !Array.isArray(cmSites) || !cmSites.length) return;
        const coneHex = jammingActive ? 0xff4d4d : 0xf39c12;
        const coneOpacity = jammingActive ? 0.26 : 0.14;
        for (const cm of cmSites) {
            const root = new THREE.Group();
            root.position.copy(world(cm.x, cm.y, 0));
            const mast = new THREE.Mesh(
                new THREE.CylinderGeometry(1.2, 2.5, cm.z, 8),
                new THREE.MeshStandardMaterial({color: 0x3d2428, roughness: 0.45, metalness: 0.6})
            );
            mast.position.y = cm.z / 2;
            mast.userData.inspectText = `${cm.name} · 挂高 ${cm.z} m · 压制扇区 ${cm.fov}° / ${cm.range} m · ${cm.authority || '法定授权点位'}`;
            root.add(mast);

            const panel = new THREE.Mesh(
                new THREE.ConeGeometry(8.5, 16, 6),
                new THREE.MeshStandardMaterial({
                    color: coneHex, emissive: coneHex, emissiveIntensity: jammingActive ? 0.65 : 0.25, roughness: 0.35
                })
            );
            panel.rotation.x = Math.PI / 2;
            panel.rotation.z = -(cm.azimuth || 45) * Math.PI / 180;
            panel.position.y = cm.z + 4;
            panel.userData.inspectText = mast.userData.inspectText;
            root.add(panel);

            if (showLabels !== false) {
                const badge = makeLabelSprite(
                    `${jammingActive ? '⚡ 压制开启 · ' : '⚡ '}${cm.name}`,
                    jammingActive ? '#ff6b6b' : '#f5b041'
                );
                badge.position.set(0, cm.z + 20, 0);
                root.add(badge);
            }
            this.counterGroup.add(root);

            // Green EMC RF-blocking safety isolation ring (180m)
            const isoR = cm.rf_isolation_m || 180;
            const ringPts = [];
            for (let i = 0; i <= 64; i++) {
                const a = (i / 64) * Math.PI * 2;
                ringPts.push(world(cm.x + Math.sin(a) * isoR, cm.y + Math.cos(a) * isoR, 2.2));
            }
            const isoLine = new THREE.Line(
                new THREE.BufferGeometry().setFromPoints(ringPts),
                new THREE.LineDashedMaterial({color: 0x2ecc71, dashSize: 16, gapSize: 10, transparent: true, opacity: 0.88})
            );
            isoLine.computeLineDistances();
            isoLine.raycast = () => {};
            this.counterGroup.add(isoLine);

            // Translucent 3D directional suppression envelope sector
            const steps = 16, fov = cm.fov || 85, reach = cm.range || 540;
            const startDeg = (cm.azimuth || 45) - fov / 2;
            const orig = world(cm.x, cm.y, cm.z);
            const verts = [], outlinePts = [orig];
            for (let k = 0; k <= steps; k++) {
                const a1 = (startDeg + (fov * k) / steps) * Math.PI / 180;
                outlinePts.push(world(cm.x + Math.sin(a1) * reach, cm.y + Math.cos(a1) * reach, 155));
                if (k < steps) {
                    const a2 = (startDeg + (fov * (k + 1)) / steps) * Math.PI / 180;
                    const p1 = world(cm.x + Math.sin(a1) * reach, cm.y + Math.cos(a1) * reach, 155);
                    const p2 = world(cm.x + Math.sin(a2) * reach, cm.y + Math.cos(a2) * reach, 155);
                    verts.push(orig.x, orig.y, orig.z, p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
                }
            }
            outlinePts.push(orig);
            const fanGeom = new THREE.BufferGeometry();
            fanGeom.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
            const fan = new THREE.Mesh(
                fanGeom,
                new THREE.MeshBasicMaterial({color: coneHex, transparent: true, opacity: coneOpacity, side: THREE.DoubleSide, depthWrite: false})
            );
            fan.raycast = () => {};
            const fanBorder = new THREE.Line(
                new THREE.BufferGeometry().setFromPoints(outlinePts),
                new THREE.LineBasicMaterial({color: coneHex, transparent: true, opacity: 0.82})
            );
            fanBorder.raycast = () => {};
            this.counterGroup.add(fan, fanBorder);
        }
    }

    update(payload) {
        this.currentState = payload;
        this._ensureSatelliteImagery(payload.anchor, payload.showImagery);

        const sceneSig = JSON.stringify([
            payload.sceneId, payload.buildingSource,
            payload.buildings?.length, payload.buildings?.[0],
            payload.footprints?.length, payload.zone,
        ]);
        if (sceneSig !== this.sceneSignature) {
            this.sceneSignature = sceneSig;
            this._rebuildBuildings(payload.buildings || [], payload.footprints || [], payload.zone);
        }

        const devSig = JSON.stringify([payload.devices, payload.addedIds, payload.showFov, payload.showLabels]);
        if (devSig !== this.deviceSignature) {
            this.deviceSignature = devSig;
            this._rebuildDevices(payload.devices || [], payload.addedIds || [], payload.showFov, payload.showLabels);
        }

        const cmSig = JSON.stringify([payload.countermeasureSites, payload.showCountermeasure, payload.jammingActive, payload.showLabels]);
        if (cmSig !== this.counterSignature) {
            this.counterSignature = cmSig;
            this._rebuildCountermeasures(payload.countermeasureSites || [], payload.showCountermeasure, payload.jammingActive, payload.showLabels);
        }

        const trkSig = JSON.stringify([
            payload.points?.length, payload.points?.[0], payload.points?.at?.(-1),
            payload.timeline?.map(s => s.state).join(''), payload.segmentRange,
        ]);
        if (trkSig !== this.trackSignature) {
            this.trackSignature = trkSig;
            this._rebuildTrack(payload.points, payload.timeline, payload.segmentRange);
        }

        clearGroup(this.losGroup);
        clearGroup(this.guidanceGroup);
        if (!Array.isArray(payload.points) || !payload.points.length) {
            this.actorGroup.visible = false;
            return;
        }
        const idx = Math.max(0, Math.min(payload.points.length - 1, payload.timeIndex || 0));
        const [tx, ty, tz] = payload.points[idx];
        const nextPt = payload.points[Math.min(payload.points.length - 1, idx + 1)];
        const prevPt = payload.points[Math.max(0, idx - 1)];
        const heading = Math.atan2(nextPt[0] - prevPt[0], nextPt[1] - prevPt[1]);

        this.actorGroup.visible = true;
        const uavPos = world(tx, ty, tz);
        this.drone.position.copy(uavPos);
        this.drone.rotation.set(0, -heading, 0);

        // Cooperative whitelist UAV on corridor path for airspace deconfliction visualization
        if (payload.coopPoint && payload.showCountermeasure !== false) {
            const [cx, cy, cz, cHeading] = payload.coopPoint;
            const cWorld = world(cx, cy, cz);
            this.coopDrone.visible = true;
            this.coopDrone.position.copy(cWorld);
            this.coopDrone.rotation.set(0, -(cHeading || 0), 0);
            this.coopBadge.visible = payload.showLabels !== false;
            this.coopBadge.position.copy(cWorld).add(new THREE.Vector3(0, 22, 0));
        } else {
            this.coopDrone.visible = false;
            this.coopBadge.visible = false;
        }

        this.dropLine.geometry.setFromPoints([world(tx, ty, 0.5), uavPos]);
        this.dropLine.computeLineDistances();

        const sample = payload.timeline?.[idx] || {};
        const ellipsoidAxes = payload.ellipsoids?.[idx];
        const showEllipsoid = payload.showEllipsoid !== false;
        this.ellipsoidMesh.visible = showEllipsoid;
        this.ellipsoidWire.visible = showEllipsoid;
        if (showEllipsoid) {
            const effState = payload.effectiveState || sample.state;
            const stHex = STATE_HEX[effState] || 0x2ecc71;
            this.ellipsoidMesh.material.color.setHex(stHex);
            this.ellipsoidWire.material.color.setHex(stHex);
            this.ellipsoidMesh.position.copy(uavPos);
            this.ellipsoidWire.position.copy(uavPos);
            let rx = 26, ry = 18, rz = 26;
            if (payload.jammingDegraded) {
                rx = rz = 66;
                ry = 46;
            } else if (Array.isArray(ellipsoidAxes) && ellipsoidAxes.length === 3) {
                rx = Math.max(8, Math.min(75, ellipsoidAxes[1]));
                ry = Math.max(8, Math.min(60, ellipsoidAxes[0]));
                rz = Math.max(8, Math.min(75, ellipsoidAxes[2]));
            } else if (Number.isFinite(sample.crlb_m)) {
                const r = Math.max(9, Math.min(72, sample.crlb_m * 0.85));
                rx = rz = r;
                ry = r * 0.72;
            } else {
                rx = rz = 56;
                ry = 40;
            }
            this.ellipsoidMesh.scale.set(rx, ry, rz);
            this.ellipsoidWire.scale.set(rx, ry, rz);
        }

        if (payload.showLos !== false) {
            for (const d of payload.devices || []) {
                if (d.status === 'offline' || !inSensorFov(d, tx, ty, tz)) continue;
                const sPos = world(d.x, d.y, d.z);
                if (payload.jammingActive && (d.type === 'RF' || d.type === 'ADSB')) {
                    const mutedLine = new THREE.Line(
                        new THREE.BufferGeometry().setFromPoints([sPos, uavPos]),
                        new THREE.LineDashedMaterial({color: 0x9b59b6, dashSize: 8, gapSize: 10, transparent: true, opacity: 0.42})
                    );
                    mutedLine.computeLineDistances();
                    mutedLine.raycast = () => {};
                    this.losGroup.add(mutedLine);
                    continue;
                }
                const hit = rayOccluded(d.x, d.y, d.z, tx, ty, tz, payload.buildings || []);
                if (!hit) {
                    const beamHex = d.type === 'EO' || d.type === 'IR' ? 0xf5b041 : (d.type === 'RF' ? 0xc58af9 : 0x3bf5b5);
                    const beam = new THREE.Mesh(
                        new THREE.TubeGeometry(new THREE.LineCurve3(sPos, uavPos), 1, 1.45, 6, false),
                        new THREE.MeshBasicMaterial({color: beamHex, transparent: true, opacity: 0.82})
                    );
                    beam.raycast = () => {};
                    this.losGroup.add(beam);
                } else {
                    const hPos = world(...hit);
                    const blockedLine = new THREE.Line(
                        new THREE.BufferGeometry().setFromPoints([sPos, hPos]),
                        new THREE.LineDashedMaterial({color: 0xff5e57, dashSize: 12, gapSize: 8, transparent: true, opacity: 0.85})
                    );
                    blockedLine.computeLineDistances();
                    blockedLine.raycast = () => {};
                    const marker = new THREE.Mesh(
                        new THREE.SphereGeometry(5.2, 10, 8),
                        new THREE.MeshBasicMaterial({color: 0xff5252})
                    );
                    marker.position.copy(hPos);
                    marker.raycast = () => {};
                    this.losGroup.add(blockedLine, marker);
                }
            }
        }

        // 3D Primitive 3: Narrow directional countermeasure guidance cone & cooperative airspace deconfliction
        if (payload.showCountermeasure !== false && payload.guidance?.station) {
            const g = payload.guidance;
            const cmPos = world(g.station.x, g.station.y, g.station.z);
            const gHex = g.conflict ? 0xf1c40f : (g.ready ? 0x00e5ff : 0xe67e22);
            const dist = cmPos.distanceTo(uavPos);
            if (dist > 5 && (g.ready || g.conflict)) {
                const guideLine = new THREE.Line(
                    new THREE.BufferGeometry().setFromPoints([cmPos, uavPos]),
                    new THREE.LineDashedMaterial({color: gHex, dashSize: 14, gapSize: 7, transparent: true, opacity: 0.95})
                );
                guideLine.computeLineDistances();
                guideLine.raycast = () => {};
                const mid = cmPos.clone().lerp(uavPos, 0.5);
                const cone = new THREE.Mesh(
                    new THREE.ConeGeometry(Math.min(28, dist * 0.048), dist, 14, 1, true),
                    new THREE.MeshBasicMaterial({color: gHex, transparent: true, opacity: g.conflict ? 0.22 : 0.18, side: THREE.DoubleSide, depthWrite: false})
                );
                cone.position.copy(mid);
                cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), cmPos.clone().sub(uavPos).normalize());
                cone.raycast = () => {};
                this.guidanceGroup.add(guideLine, cone);
                if (payload.showLabels !== false) {
                    const tag = makeLabelSprite(
                        g.conflict
                            ? `⚠ 扇区含合作机 · 暂缓压制 · 光电持续取证`
                            : `⚡ 合规引导就绪 · 方位 ${g.azimuth}° / 俯仰 ${g.elevation}°`,
                        g.conflict ? '#f1c40f' : '#00e5ff'
                    );
                    tag.position.copy(uavPos).add(new THREE.Vector3(0, 26, 0));
                    this.guidanceGroup.add(tag);
                }
            }
        }
    }
}

window.TwinEngine3D = {
    mount(host) {
        return new TwinEngine(host);
    },
};
window.dispatchEvent(new Event('twin-engine3d-ready'));
