# 스타일 지침 — 복셀 (로블록스·마인크래프트류)

모든 형태가 **같은 크기의 정육면체 격자**로만 이루어진다. 곡선도, 경사도, 임의 각도도
없다. 형태의 표현력은 곡률이 아니라 **계단식 윤곽과 색 블록의 배치**에서 나온다.

1. **`lp.voxel` 계열만 쓴다**: 모든 파트는 `lp.voxel` / `lp.voxel_box` / `lp.voxel_column`으로 만들어라. `box`·`cylinder`·`lathe`·`prism`·`tube`·`sphere`를 쓰지 마라 — 격자에서 벗어나는 순간 복셀로 읽히지 않는다.
2. **격자 크기를 하나로 고정하라**: 코드 첫머리에 `CELL = 0.2` 처럼 셀 크기를 한 번 정하고 모든 `size=`에 그 값을 그대로 넘겨라. 파트마다 셀 크기가 다르면 격자가 깨진다. 프랍은 0.1~0.2m, 건물·캐릭터는 0.2~0.3m 셀이 적당하다.
3. **원점도 격자에 맞춘다**: `origin`의 각 성분은 셀 크기의 정수배여야 한다 (`origin=(2*CELL, 0, 0)`). 실수 좌표를 직접 쓰지 마라.
4. **변형기 전면 금지**: `lp.bend`·`lp.bulge`·`lp.taper`·`lp.shear`·`lp.jitter`·`lp.bevel`은 격자를 무너뜨린다. 쓰지 마라. 모양의 변화는 **채우는 셀을 바꿔서** 만든다.
5. **곡선은 계단으로 표현하라**: 둥근 지붕·원통 몸통은 셀을 계단식으로 줄여 근사한다. 예: 반지름 4셀 원기둥의 한 층은 `[(i, j, k) for i in range(-4, 5) for j in range(-4, 5) if i*i + j*j <= 16]`.
6. **폴리 버짓 (트라이 기준)**: 모델 전체 ≤ 12000. 맞닿은 안쪽 면은 `lp.voxel`이 알아서 빼므로 셀을 붙여 쌓는 것은 싸다. 대신 **셀을 잘게 쪼개지 마라** — 한 변 40셀이 넘어가면 표면적만 늘고 복셀 느낌은 오히려 사라진다. 한 변 8~24셀이 적정선이다.
7. **색은 파트 단위 블록으로**: 셀 하나하나를 다른 색으로 칠하려 하지 마라. 같은 역할의 셀 덩어리를 `lp.voxel` 한 번으로 만들고 그 덩어리에 색을 입혀라 (지붕 덩어리 / 벽 덩어리 / 창문 덩어리). 색은 4~8색, 채도 높고 경계가 또렷하게 — 그라데이션이나 중간 톤을 만들지 마라.
8. **디테일은 1~2셀 돌출로**: 문·창문·손잡이·눈은 본체 표면에서 1셀 튀어나오거나 들어간 덩어리로 표현한다. 셀보다 작은 디테일은 만들 수 없다 — 표현하고 싶으면 셀 크기를 줄이는 게 아니라 대상 전체를 키워라.

## 좋은 예시 (참고 패턴)

```python
# 복셀 오두막: 셀 크기 고정 + 계단식 박공지붕 + 1셀 돌출 디테일 (시그니처: 박공지붕 + 문 + 굴뚝)
CELL = 0.2
walls = lp.voxel_box("Walls", dims=(10, 8, 6), size=CELL, hollow=True)   # 속을 비워 트라이 절감
lp.set_color(walls, (0.93, 0.87, 0.72))
roof_cells = []
for layer in range(5):                      # 층을 올릴수록 폭을 줄여 박공을 계단으로 근사
    for x in range(layer, 10 - layer):
        for y in range(-1, 9):
            roof_cells.append((x, y, 6 + layer))
roof = lp.voxel("Roof", roof_cells, size=CELL)
lp.set_color(roof, (0.85, 0.35, 0.30))
door = lp.voxel("Door", [(4, -1, k) for k in range(3)] + [(5, -1, k) for k in range(3)],
                size=CELL)                  # 벽 앞으로 1셀 돌출
lp.set_color(door, (0.48, 0.32, 0.20))
chimney = lp.voxel_column("Chimney", height=4, size=CELL, origin=(7 * CELL, 5 * CELL, 8 * CELL))
lp.set_color(chimney, (0.55, 0.50, 0.50))
hut = lp.join([walls, roof, door, chimney], name="VoxelHut")
```

```python
# 복셀 나무: 기둥 + 계단식 원형 잎 덩어리 2단 (곡선을 셀 조건식으로 근사)
CELL = 0.25
trunk = lp.voxel_column("Trunk", height=5, size=CELL)
lp.set_color(trunk, (0.42, 0.27, 0.13))
canopy = []
for k, radius in ((5, 3), (6, 3), (7, 2)):   # 위로 갈수록 좁아지는 잎 덩어리
    canopy += [(i, j, k) for i in range(-radius, radius + 1) for j in range(-radius, radius + 1)
               if i * i + j * j <= radius * radius]
leaves = lp.voxel("Leaves", canopy, size=CELL)
lp.set_color(leaves, (0.35, 0.62, 0.28))
tree = lp.join([trunk, leaves], name="VoxelTree")
```
