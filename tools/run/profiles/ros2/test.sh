#!/usr/bin/env bash
# ROS 2 시험 실행 (컨테이너 안에서 돈다): colcon test를 기본 순차로 실행한다.
#   같은 컨테이너·같은 ROS_DOMAIN_ID에서 패키지 시험을 병렬로 돌리면 DDS 시험끼리 토픽을 주고받아 거짓 실패가 난다.
#   ROS_TEST_PARALLEL=1이면 병렬. 추가 인자는 colcon test에 넘긴다.
#   사용: scripts/run/run.sh 'scripts/run/profiles/ros2/test.sh --packages-select <pkg>'
set -euo pipefail
cd "${ROS_WS:-ws}"
exec_arg=(--executor sequential); [ "${ROS_TEST_PARALLEL:-0}" = "1" ] && exec_arg=()
colcon test "${exec_arg[@]}" --event-handlers console_direct+ "$@"
colcon test-result --verbose
