# 스타일 지침 — 로우폴리 캐주얼

캐주얼 모바일 게임의 로우폴리 에셋이다. 각진 면과 플랫 셰이딩으로 읽히고,
채도 높은 색 몇 가지가 형태를 구분한다. 사실적인 질감이 아니라 **단순한 덩어리의
대비**로 완성도를 만든다.

1. **베벨 기본 금지**: `lp.bevel`은 기본적으로 사용하지 마라. 육면체는 날카로운 모서리 그대로 두는 것이 로우폴리다. 예외: 사용자가 "베벨/모서리 둥글게/부드러운(소프트) 스타일"을 요청한 경우에만, 실루엣을 결정하는 큰 파트에 한해 width 0.01~0.03, segments=1로 절제해서 사용하라.
2. **폴리 버짓 (트라이 기준)**: 모델 전체 ≤ 10000. 넉넉한 버짓이다 — 아끼려고 형태를 희생하지 마라. 실루엣을 결정하는 곡률(원기둥/lathe 세그먼트 8~16, `sphere` subdivisions 1~2)과 디테일 파트에 적극 투자하라. 단 실루엣에 기여하지 않는 면(평면의 불필요한 분할·과도한 세그먼트·겹침 은면)에 낭비하지는 마라 — 버짓이 남는 것은 문제가 아니지만 형태가 빈약한 것은 문제다.
3. **데포르메 변형기를 적극 사용하라**: `lp.bend`(구부림), `lp.bulge`(배불림/잘록), `lp.shear`(기울임), `lp.stretch_at`(특정 높이만 과장), `lp.taper`(끝 좁힘), `lp.jitter`(유기물 표면). 프리미티브를 그대로 두지 말고 변형기로 실루엣에 성격을 부여하라.
4. **정량 과장 (데포르메의 공식)**: 사실 비율은 심심하다. 모델의 정체성을 만드는 특징 부위(지붕·굴뚝·바퀴·손잡이·문·잎덩어리)는 사실 대비 **130~160%**로 키우고, 지지 부위(벽·다리·기둥)는 80~90%로 줄여 대비를 만들어라. `lp.stretch_at`/`lp.bulge`/`lp.taper`가 이 용도의 도구다.
5. **곡률 대비**: 모델당 최소 1개는 곡률 실루엣(`lathe smooth`/`bulge`/`bend`)을 넣어라. 전부 직선이면 미완성으로 보이고, 직선 파트 옆의 곡선 파트가 서로를 돋보이게 한다.
6. **로우폴리 = 최소 면, 박스 조립이 아니다**: 로우폴리는 "실루엣의 꺾임마다 면 하나"로 형태를 읽히게 하는 것이다. 본체를 box 하나로, 또는 box 여러 개를 쌓아 만들지 마라 — `silhouette`(정면·측면·평면 윤곽 교차)·`loft`(단면 이어 붙이기)·`prism`·`lathe`로 **한 덩어리 껍질**을 만들고, 휠 아치·관통 구멍처럼 실제로 뚫린 곳만 `cut`으로 파라. 창·문은 파지 말고 틀 판 + 유리(문짝) 판을 덧붙여라. box는 작은 보조 디테일에만 써라.
7. **색은 3~6색, 재질·부위마다 고유색 하나**: 같은 재질을 밝은 톤/어두운 톤으로 나눠 명암을 칠하지 마라 — 명암은 플랫 셰이딩과 조명이 만든다. 색은 재질이나 부위가 실제로 다를 때만 나눠라(차체 빨강·창 유리·고무 검정, 여우 주황·가슴털 크림). 무늬·얼룩은 `set_color(inside=영역)`, 높이 경계(양말·밑동)는 `below=/above=`로 칠하라. 캐주얼 톤: 채도 높고 밝게.

## 좋은 예시 (참고 패턴)

```python
# 낡은 나무 배럴: lathe 곡률 실루엣 + 디테일 밴드 + 재질별 배색
body = lp.lathe("Body", profile=[(0.28, 0.0), (0.36, 0.2), (0.40, 0.45),
                                 (0.36, 0.7), (0.28, 0.9)], segments=10)
lp.set_color(body, (0.55, 0.36, 0.18))          # 나무
lid = lp.cylinder("Lid", radius=0.26, depth=0.04, segments=10, location=(0, 0, 0.9))
lp.set_color(lid, (0.55, 0.36, 0.18))            # 같은 나무 — 명암을 색으로 칠하지 않는다
bands = []
for z in (0.18, 0.72):                            # 금속 밴드 2개
    band = lp.lathe(f"Band{z}", profile=[(0.375, z - 0.03), (0.395, z), (0.375, z + 0.03)],
                    segments=10, location=(0, 0, 0))
    lp.set_color(band, (0.45, 0.47, 0.52))
    bands.append(band)
barrel = lp.join([body, lid, *bands], name="Barrel")
```

```python
# 데포르메 자동차: silhouette 단일 본체 + cut 휠 아치(관통 구멍) + 덧붙인 창(틀 판 + 유리 판) — 차체를 파지 않아 면이 쪼개지지 않음
body = lp.silhouette("Body",
    front=[(-0.8, 0.15), (0.8, 0.15), (0.82, 0.65), (0.6, 1.3), (-0.6, 1.3), (-0.82, 0.65)],
    side=[(-1.7, 0.2), (1.7, 0.2), (1.75, 0.7), (1.35, 0.8), (1.0, 1.3), (-0.3, 1.3),
          (-0.85, 0.78), (-1.75, 0.68)],                 # -y가 앞: 낮은 보닛 → 경사 앞유리 → 짧은 트렁크
    top=[(-0.7, -1.75), (0.7, -1.75), (0.82, -1.3), (0.82, 1.4), (0.72, 1.75), (-0.72, 1.75),
         (-0.82, 1.4), (-0.82, -1.3)])                   # 위에서 본 앞뒤 모서리 깎임
lp.set_color(body, (0.85, 0.25, 0.22))                   # 색은 cut 전에
for y in (-1.05, 1.05):                                  # 휠 아치: 차체를 관통하는 원기둥으로 파냄
    arch = lp.cylinder(f"Arch{y}", radius=0.4, depth=2.0, segments=10,
                       location=(0, y, 0.3), rotation=(0, 1.5708, 0))
    lp.set_color(arch, (0.15, 0.13, 0.13))
    lp.cut(body, arch)
windows = []
for key, (w, h), loc, toward in (("Side", (1.2, 0.26), (1.5, 0.1, 1.0), (-1, 0, 0)),   # +x 옆창(미러로 반대편)
                                 ("Shield", (1.0, 0.4), (0, -2.0, 1.04), (0, 1, 0))):  # 앞유리: 앞에서 경사면으로
    frame = lp.box(f"{key}Frame", size=(w + 0.08, 0.02, h + 0.08), location=loc)   # 틀 판: 유리보다 조금 크고 얇게
    lp.set_color(frame, (0.12, 0.12, 0.14))
    glass = lp.box(f"{key}Glass", size=(w, 0.03, h), location=loc)                 # 유리 판: 틀보다 1cm 더 나와 테두리가 남는다
    lp.set_color(glass, (0.55, 0.75, 0.85))
    for pane in (frame, glass):
        lp.attach(pane, body, direction=toward, sink=0.01, align=True)              # 경사진 표면을 따라 눕힌다
    windows += [frame, glass]
wheel = lp.cylinder("WheelL", radius=0.34, depth=0.24, segments=10,
                    location=(0.72, -1.05, 0.34), rotation=(0, 1.5708, 0))
lp.set_color(wheel, (0.18, 0.17, 0.17))
rear = lp.cylinder("WheelRL", radius=0.34, depth=0.24, segments=10,
                   location=(0.72, 1.05, 0.34), rotation=(0, 1.5708, 0))
lp.set_color(rear, (0.18, 0.17, 0.17))
lamp = lp.cylinder("LampL", radius=0.12, depth=0.1, segments=8,
                   location=(0.52, -1.74, 0.6), rotation=(1.5708, 0, 0))
lp.set_color(lamp, (0.98, 0.92, 0.6))
half = lp.join([wheel, rear, lamp, windows[0], windows[1]], name="SideParts")
lp.mirror_x(half)                                        # 부착물은 반쪽만 만들고 대칭
car = lp.join([body, half, windows[2], windows[3]], name="Car")
```

```python
# 폭격으로 부서진 돌담: 계단형 파단면 + 그을림 파트 분할 + 잔해 쌓기 (공통 규칙 13의 코드 패턴)
wall = lp.prism("Wall", outline=[(-1.4, 0.0), (0.1, 0.0), (0.1, 1.1), (0.5, 1.1), (0.35, 1.6), (-1.4, 1.6)],
                depth=0.3)                            # 오른쪽 윤곽이 계단형으로 무너진 실루엣
lp.set_color(wall, (0.72, 0.68, 0.60))                # 돌
scorch = lp.prism("Scorch", outline=[(0.1, 0.0), (0.8, 0.0), (0.65, 0.7), (0.5, 1.1), (0.1, 1.1)],
                  depth=0.3)                          # 그을림 = 파단면 주변 벽을 별도 파트로 나눠 어두운 톤 (검은 판 부착 금지)
lp.set_color(scorch, (0.38, 0.34, 0.30))
debris = []
positions = [(0.85, 0.1), (1.15, -0.15), (0.7, -0.3), (1.0, 0.35), (1.35, 0.1), (0.55, 0.25)]
for i, (x, y) in enumerate(positions):                # 잔해: 서로 떨어뜨려 배치 — 파편끼리 관통 금지
    s = 0.26 - i * 0.03                               # 갈수록 작은 파편
    d = lp.box(f"Debris{i}", size=(s, s * 0.8, s * 0.6),
               location=(x, y, s * 0.25), rotation=(0, 0, i * 0.9))  # 지면에만 살짝 파묻힘
    lp.set_color(d, (0.72, 0.68, 0.60))           # 벽과 같은 돌
    debris.append(d)
ruin = lp.join([wall, scorch, *debris], name="RuinedWall")
```
