# ROS 2 프로필 — run.sh가 source한다. 격리 변수와 명령 앞 setup을 정한다.
#   ROS_DISTRO_SETUP: 컨테이너 안 setup 스크립트 (기본 /opt/ros/humble/setup.bash)
#   RUN_ROS_DOMAIN_ID: 지정하면 그 값, 없으면 워크트리 경로 해시(1~100). 해시는 충돌하므로 격리는 전용 네트워크가 맡는다
#   GZ_PARTITION: 컨테이너 이름. 같은 월드·모델 이름을 쓰는 동시 시뮬이 gz-transport에서 섞이지 않게 한다
profile_env() {
  local dom="${RUN_ROS_DOMAIN_ID:-$(( $(printf '%s' "$ROOT" | cksum | cut -d' ' -f1) % 100 + 1 ))}"
  ISO_ENV+=(-e "ROS_DOMAIN_ID=$dom" -e "GZ_PARTITION=$NAME" -e HEADLESS=1)
  # bash -lc는 ROS setup을 자동으로 읽지 않는다 → 명령 앞에 붙인다
  WRAP_PREFIX="source ${ROS_DISTRO_SETUP:-/opt/ros/humble/setup.bash}${ROS_WS_SETUP:+ && source $ROS_WS_SETUP}"
  PASS_ENV="${PASS_ENV}|^(ROS_|RMW_|GZ_)"
}
