#include "gtest/gtest.h"

TEST(FirstTest, Testing)
{
	EXPECT_EQ(1, 1);
	EXPECT_NE(1, 2);
}

TEST(FirstTest, Testeroo)
{
	EXPECT_EQ(1, 2) << "wowza";
	EXPECT_NE(1, 5);
}

