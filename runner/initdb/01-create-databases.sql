-- Init script : crée les bases CI isolées par service (évite les conflits entre sessions parallèles)
CREATE DATABASE ratis_test_rewards;
CREATE DATABASE ratis_test_auth;
CREATE DATABASE ratis_test_notifier;
CREATE DATABASE ratis_test_analyser;
CREATE DATABASE ratis_test_consensus;
CREATE DATABASE ratis_test_off_sync;
CREATE DATABASE ratis_test_purge;
CREATE DATABASE ratis_dev;
