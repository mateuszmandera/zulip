class zulip::sharding {
  include zulip::base
  include zulip::common
  include zulip::nginx

  exec { 'sharding_script':
    subscribe => File['/etc/zulip/zulip.conf'],
    notify    => Service['nginx'],
    command   => '/home/zulip/deployments/current/scripts/lib/sharding.py',
    onlyif    => 'test -f /home/zulip/deployments/current/scripts/lib/sharding.py\
  -a ! -f /home/zulip/deployments/next/scripts/lib/sharding.py',
  }
  exec { 'sharding_script_next':
    subscribe => File['/etc/zulip/zulip.conf'],
    notify    => Service['nginx'],
    command   => '/home/zulip/deployments/next/scripts/lib/sharding.py',
    onlyif    => 'test -f /home/zulip/deployments/next/scripts/lib/sharding.py',
  }
  file { '/etc/zulip/nginx_sharding.conf':
    ensure  => file,
    require => User['zulip'],
    owner   => 'zulip',
    group   => 'zulip',
    mode    => '0640',
    notify  => Service['nginx'],
    content => "set \$tornado_server http://tornado;\n",
    replace => false,
  }
  file { '/etc/zulip/sharding.json':
    ensure  => file,
    require => User['zulip'],
    owner   => 'zulip',
    group   => 'zulip',
    mode    => '0640',
    content => "{}\n",
    replace => false,
  }
}
