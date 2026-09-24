package xyz.unsponsor.backend;

import org.springframework.boot.SpringApplication;

public class TestUnsponsorBackendApplication {

	public static void main(String[] args) {
		SpringApplication.from(UnsponsorBackendApplication::main).with(TestcontainersConfiguration.class).run(args);
	}

}
