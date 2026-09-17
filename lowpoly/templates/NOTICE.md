# 베이스 메시 템플릿 출처

`humanoid_*.obj` 4종은 Blender Studio의 **Human Base Meshes Bundle v1.4.1**
(https://www.blender.org/download/demo-files/ → Asset Bundles)에서 가져온 전신 베이스 메시
(`GEO-body_male_stylized`, `GEO-body_female_stylized`, `GEO-body_male_realistic`,
`GEO-body_female_realistic`)다.

- 라이선스: **CC0 1.0** (퍼블릭 도메인 헌정) — 저작권 표시 의무 없음. 감사의 뜻으로 출처를 남긴다.
- 가공: 모디파이어(Multires) 제거 후 베이스 레벨만, 발바닥 z=0·중심 X=Y=0 정렬, 머티리얼·UV 제거,
  OBJ로 저장(쿼드 보존). 형상·토폴로지는 원본 그대로다.
- 용도: 캐릭터 이미지→3D 셰이프에 슈링크랩으로 입혀 애니메이션용 쿼드 토폴로지(얼굴·관절 루프,
  부위별 밀도)를 얻는 리토폴로지 템플릿. 사용자는 `템플릿 불러오기`로 씬에 가져와 수정한 뒤
  `선택 메시 등록`으로 자기 템플릿으로 등록할 수 있다.

네발형 템플릿(`QUADRUPED`)은 CC0 소스가 없어 절차 생성 원형만 제공한다.
