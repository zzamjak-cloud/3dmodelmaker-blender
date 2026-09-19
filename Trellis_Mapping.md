TRELLIS의 텍스처 매핑(Mapping) 프로세스는 3D 공간의 색상·재질 정보를 2D 평면 이미지로 펼치는 일반적인 3D 그래픽스 매핑 방식과는 크게 다릅니다.

TRELLIS는 2D 투영 방식 대신 **3D 볼륨(Spatiotemporal/Voxel) 공간 자체에 텍스처 Latent 정보를 먼저 채워 넣은 뒤, 메쉬 생성과 함께 UV Unwrapping 및 PBR 재질 매핑을 자동 추출**하는 **Structured Latent 매핑 메커니즘**을 사용합니다.

---

### 1. 매핑 방식의 핵심 흐름

#### ① 3D Voxel / Latent 공간 매핑 (Spatial Latent Mapping)

가장 먼저, 입력받은 2D 이미지의 색상, 텍스처, 명암 정보를 분석하여 **3D 공간(Voxel Grid)** 상에 3차원 위치별(X, Y, Z) Latent 벡터 형태로 색상 및 재질 분포를 1차 배치합니다.

* 2D 이미지를 3D 표면에 단순히 **"붙이는(Projecting)"** 개념이 아니라, **"3D 공간 전체에 3D 재질 데이터를 채워 넣는(Volumetric Mapping)"** 방식입니다.

#### ② 3D Gaussian / Radiance Field 매핑

3D 공간에 매핑된 Latent 정보는 1차적으로 **3D Gaussian Splatting** 또는 **Radiance Fields** 데이터로 표출됩니다.

* 각 3D 점(Gaussian Point)마다 색상(RGB), 투명도(Opacity), 빛의 반사 특성이 직접 할당되어 있어, 카메라를 어느 각도로 돌려도 연속적이고 시점 의존적(View-dependent)인 광택과 입체감이 실시간 매핑됩니다.

#### ③ UV Unwrapping 및 PBR Map 자동 추출 (3D Mesh 변환 시)

최종 결과를 게임 엔진이나 Blender 같은 3D 툴용 메쉬(Mesh)로 내보낼 때 진행되는 매핑 단계입니다.

1. **입체 표면 메쉬화:** Marching Cubes/FlexiCubes 계열 알고리즘으로 3D 형태(Geometry)의 겉표면 메쉬를 추출합니다.
2. **자동 UV 펼치기(Auto-UV Parameterization):** 추출된 3D 메쉬를 2D 평면 UV 좌표계로 자동으로 펼칩니다.
3. **PBR Texture Baking:** 3D 공간 Latent에 채워져 있던 색상/재질 정보를 UV 좌표에 맞춰 2D 텍스처 이미지 패키지로 구워냅니다(Bake).
* **Albedo (Diffuse Map):** 순수 기본 색상
* **Normal Map:** 입체 표면 질감 및 디테일
* **Roughness / Metallic Map:** 거칠기 및 금속 반사율



---

### 2. 기존 매핑 방식과의 차이점

| 구분 | **기존 2D Projector 방식 (예: 2D-to-3D)** | **TRELLIS 매핑 방식** |
| --- | --- | --- |
| **매핑 방식** | 정면 사진을 3D 입체 위에 빔프로젝터처럼 쏘아 붙임 | **3D 옥셀(Voxel) 공간 전체에 3D Latent 매핑** |
| **측면/뒷면** | 정면 텍스처가 늘어나거나(Stretching), 보이지 않는 곳의 색상이 깨짐 | **AI가 3D 공간 상에서 뒷면의 텍스처까지 직접 채워넣어 완전함** |
| **재질 표현** | 단순 이미지 색상(RGB) 위주 | **PBR 재질(Normal, Roughness, Metallic 등) 자동 3D 매핑** |

### 요약

TRELLIS의 매핑은 "2D 그림을 3D 물체에 덧붙이는 것"이 아니라, "AI가 3D 공간 내부와 표면에 3D 재질 데이터를 먼저 직조해 넣은 뒤, 이를 3D 파일(UV/PBR)로 정교하게 추출해 내는 방식"입니다.