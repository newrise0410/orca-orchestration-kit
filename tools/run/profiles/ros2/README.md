# ROS 2 프로필 (실행 격리 모듈 확장)

`scripts/run/run.conf`에 `RUN_PROFILE=ros2`를 두면 run.sh가 이 프로필을 읽는다.

| 항목 | 내용 |
|---|---|
| 격리 변수 | `ROS_DOMAIN_ID`(워크트리 해시 또는 `RUN_ROS_DOMAIN_ID`), `GZ_PARTITION`=컨테이너 이름, `HEADLESS=1` |
| 명령 앞 setup | `source $ROS_DISTRO_SETUP` (+ `ROS_WS_SETUP`). `bash -lc`는 setup을 자동으로 읽지 않는다 |
| 전달 변수 | `ROS_*`, `RMW_*`, `GZ_*` |
| 시험 | `profiles/ros2/test.sh` — colcon test 기본 순차 실행 |

## 운영 교훈 (일반화)
- **동시 시뮬 컨테이너끼리 DDS·gz 토픽이 섞인다.** 해시로 정한 `ROS_DOMAIN_ID`는 충돌하고, `GZ_PARTITION`이 없으면 gz-transport가 섞이고, 월드·모델 이름이 같으면 더 심하다. → 전용 docker 네트워크 + `GZ_PARTITION`을 기본으로 둔다. 혼선은 증상이 간헐적이라 늦게 발견되고, 이미 머지된 결과까지 다시 검증하게 만든다.
- **같은 컨테이너 안의 병렬 시험도 섞인다.** 패키지 시험을 병렬로 돌리면 같은 도메인에서 서로의 토픽을 받는다. → 기본 순차.
- **토픽 확인은 `ros2 topic list --no-daemon --spin-time 10`.** 참여자 탐색에 수 초 걸린다.
- **rclpy 노드 종료 경합.** 기본 신호 처리가 수신 도중 KeyboardInterrupt를 일으켜 종료 코드 1이 간헐적으로 난다. 골격의 `main()`이 복제되므로, 처음부터 공용 실행 도우미(신호 처리 끔 → 플래그 → spin_once 루프 → 순서대로 정리)를 두고 골격이 그것을 쓰게 한다.
- **시뮬 정답(ground truth) 토픽은 기본 이름을 피한다.** 상용 제어 스택이 기본 이름의 토픽을 추정기 입력으로 구독할 수 있다. 무엇을 구독하는지 확인한다.
- **센서 모델은 값이 그럴듯한지(범위·변화) 스모크에서 검사한다.** '토픽이 나온다'만으로는 부족하다(가려진 거리 센서가 고정값을 내던 사례).
- **추정기 내부 사건(리셋 등)이 제어에 어떤 물리적 결과를 내는지까지 시험한다.**
